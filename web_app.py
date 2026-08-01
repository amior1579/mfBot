# web_app.py
import asyncio
import threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from bot_core import run_multiple_trades, log_buffer
from db import DatabaseManager
from dotenv import load_dotenv
app = Flask(__name__)
CORS(app)

# ──────────── راه‌اندازی دیتابیس ────────────
load_dotenv()
db = DatabaseManager()  # از متغیر محیطی DATABASE_URL استفاده می‌کند

# ──────────── وضعیت ربات ────────────
bot_running = False
current_stop_event = None
bot_lock = threading.Lock()

# ===================== دریافت لاگ‌ها =====================
@app.route("/api/logs")
def get_logs():
    return jsonify(log_buffer)

# ===================== مدیریت حساب‌ها =====================
@app.route("/api/accounts", methods=["POST"])
def add_account():
    data = request.json
    name = data.get("name", "").strip()
    username = data.get("username")
    password = data.get("password")
    if not name:
        name = f"حساب {len(db.get_accounts()) + 1}"
    if username and password:
        account_id = db.add_account(name, username, password)
        return jsonify({"status": "ok", "id": account_id, "accounts": db.get_accounts()})
    return jsonify({"error": "نام کاربری و رمز عبور الزامی است"}), 400

@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    accounts = db.get_accounts()
    if any(acc['id'] == account_id for acc in accounts):
        db.delete_account(account_id)
        return jsonify({"status": "ok", "accounts": db.get_accounts()})
    return jsonify({"error": "حساب یافت نشد"}), 404

# ===================== مدیریت معاملات =====================
@app.route("/api/trades", methods=["POST"])
def add_trade():
    data = request.json
    symbol = data.get("symbol")
    order_type = data.get("type")
    amount = data.get("amount")
    target_time = data.get("target_time")
    account_id = data.get("account_id")

    # اعتبارسنجی
    if not all([symbol, order_type, amount, target_time, account_id is not None]):
        return jsonify({"error": "همه فیلدها الزامی است"}), 400
    try:
        amount = int(amount)
    except:
        return jsonify({"error": "مبلغ/تعداد باید عدد باشد"}), 400
    if order_type not in ("خرید", "فروش"):
        return jsonify({"error": "نوع معامله نامعتبر"}), 400
    import re
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", target_time):
        return jsonify({"error": "فرمت ساعت باید HH:MM:SS باشد"}), 400

    # بررسی وجود account_id
    accounts = db.get_accounts()
    if not any(acc['id'] == account_id for acc in accounts):
        return jsonify({"error": "شناسه حساب نامعتبر"}), 400

    invest_amount = amount if order_type == "خرید" else None
    quantity = amount if order_type == "فروش" else None

    trade_id = db.add_trade(
        symbol=symbol,
        order_type=order_type,
        target_time=target_time,
        account_id=account_id,
        amount=amount,
        invest_amount=invest_amount,
        quantity=quantity
    )
    return jsonify({"status": "ok", "id": trade_id, "trades": db.get_trades()})

@app.route("/api/trades/<int:trade_id>", methods=["PUT"])
def update_trade(trade_id):
    data = request.json
    symbol = data.get("symbol")
    order_type = data.get("type")
    amount = data.get("amount")
    target_time = data.get("target_time")
    account_id = data.get("account_id")

    if not all([symbol, order_type, amount, target_time, account_id is not None]):
        return jsonify({"error": "همه فیلدها الزامی است"}), 400
    try:
        amount = int(amount)
    except:
        return jsonify({"error": "مبلغ/تعداد باید عدد باشد"}), 400
    if order_type not in ("خرید", "فروش"):
        return jsonify({"error": "نوع معامله نامعتبر"}), 400
    import re
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", target_time):
        return jsonify({"error": "فرمت ساعت باید HH:MM:SS باشد"}), 400

    accounts = db.get_accounts()
    if not any(acc['id'] == account_id for acc in accounts):
        return jsonify({"error": "شناسه حساب نامعتبر"}), 400

    invest_amount = amount if order_type == "خرید" else None
    quantity = amount if order_type == "فروش" else None

    db.update_trade(
        trade_id=trade_id,
        symbol=symbol,
        order_type=order_type,
        target_time=target_time,
        account_id=account_id,
        amount=amount,
        invest_amount=invest_amount,
        quantity=quantity
    )
    return jsonify({"status": "ok", "trades": db.get_trades()})

@app.route("/api/trades/<int:trade_id>", methods=["DELETE"])
def delete_trade(trade_id):
    trades = db.get_trades()
    if any(t['id'] == trade_id for t in trades):
        db.delete_trade(trade_id)
        return jsonify({"status": "ok", "trades": db.get_trades()})
    return jsonify({"error": "معامله یافت نشد"}), 404

# ===================== تنظیمات دقت و offset =====================
@app.route("/api/precision", methods=["GET", "POST"])
def precision():
    if request.method == "POST":
        data = request.json
        try:
            val = int(data.get("precision_ms", 20))
            val = max(0, min(val, 1000))
            db.set_setting("precision_ms", str(val))
            return jsonify({"status": "ok", "precision_ms": val})
        except:
            return jsonify({"error": "مقدار نامعتبر"}), 400
    else:
        val = db.get_setting("precision_ms", "20")
        return jsonify({"precision_ms": int(val)})

@app.route("/api/offset", methods=["GET", "POST"])
def offset():
    if request.method == "POST":
        data = request.json
        try:
            val = int(data.get("offset_ms", 0))
            val = max(0, val)
            db.set_setting("click_offset_ms", str(val))
            return jsonify({"status": "ok", "offset_ms": val})
        except:
            return jsonify({"error": "مقدار نامعتبر"}), 400
    else:
        val = db.get_setting("click_offset_ms", "0")
        return jsonify({"offset_ms": int(val)})

# ===================== اجرا و توقف ربات =====================
@app.route("/api/run", methods=["POST"])
def run_bot():
    global bot_running, current_stop_event
    with bot_lock:
        if bot_running:
            return jsonify({"error": "ربات در حال اجراست. ابتدا آن را متوقف کنید."}), 400

        accounts = db.get_accounts()
        trades = db.get_trades()

        if not accounts:
            return jsonify({"error": "حداقل یک حساب تعریف کنید"}), 400
        if not trades:
            return jsonify({"error": "حداقل یک معامله تعریف کنید"}), 400

        # تبدیل account_id واقعی به ایندکس در لیست accounts برای سازگاری با bot_core
        account_id_to_index = {acc['id']: i for i, acc in enumerate(accounts)}
        for trade in trades:
            real_id = trade['account_id']
            trade['account_id'] = account_id_to_index.get(real_id, 0)

        precision_ms = int(db.get_setting("precision_ms", "20"))
        click_offset_ms = int(db.get_setting("click_offset_ms", "0"))

        stop_event = asyncio.Event()
        current_stop_event = stop_event

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    run_multiple_trades(accounts, trades, precision_ms, stop_event, click_offset_ms)
                )
            except Exception as e:
                print(f"خطا در حلقه asyncio: {e}")
            finally:
                loop.close()
                with bot_lock:
                    global bot_running
                    bot_running = False

        thread = threading.Thread(target=run_async)
        thread.start()
        bot_running = True
        return jsonify({"status": "ربات با موفقیت شروع به کار کرد"})

@app.route("/api/stop", methods=["POST"])
def stop_bot():
    global bot_running, current_stop_event
    with bot_lock:
        if not bot_running:
            return jsonify({"error": "هیچ رباتی در حال اجرا نیست"}), 400
        if current_stop_event:
            current_stop_event.set()
        bot_running = False
        return jsonify({"status": "درخواست توقف ربات ارسال شد."})

# ===================== دریافت کل تنظیمات (برای نمایش) =====================
@app.route("/api/settings", methods=["GET"])
def get_settings():
    accounts = db.get_accounts()
    trades = db.get_trades()
    precision = int(db.get_setting("precision_ms", "20"))
    offset = int(db.get_setting("click_offset_ms", "0"))
    return jsonify({
        "accounts": accounts,
        "trades": trades,
        "precision_ms": precision,
        "click_offset_ms": offset
    })

# ===================== دریافت نوتیفیکیشن‌ها =====================
@app.route("/api/notifications")
def get_notifications():
    try:
        with open("notifications.log", "r", encoding="utf-8") as f:
            lines = f.readlines()
        return jsonify(lines[-50:])
    except FileNotFoundError:
        return jsonify([])

# ===================== صفحه اصلی =====================
@app.route("/")
def index():
    return render_template("index.html")

# ===================== راه‌اندازی سرور =====================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=3000)