# web_app.py
import asyncio
import threading
import json
import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from bot_core import run_multiple_trades, log_buffer

app = Flask(__name__)
CORS(app)

# ──────────── فایل تنظیمات ────────────
SETTINGS_FILE = "settings.json"

# ──────────── ساختار داده ────────────
current_settings = {
    "accounts": [],      # هر حساب: {"name": str, "username": str, "password": str}
    "trades": [],        # هر معامله: شامل symbol, order_type, target_time, account_id, amount, invest_amount, quantity, type
    "precision_ms": 20,  # دقت زمان ارسال بر حسب میلی‌ثانیه
    "click_offset_ms": 0 # کلیک زودتر بر حسب میلی‌ثانیه
}

# ──────────── توابع بارگذاری و ذخیره ────────────
def load_settings():
    global current_settings
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                current_settings = data
                # اطمینان از وجود کلیدها
                current_settings.setdefault("accounts", [])
                current_settings.setdefault("trades", [])
                current_settings.setdefault("precision_ms", 20)
                current_settings.setdefault("click_offset_ms", 0)
        except Exception as e:
            print(f"⚠️ خطا در خواندن فایل تنظیمات: {e}")
            init_default_settings()
    else:
        init_default_settings()

def init_default_settings():
    global current_settings
    current_settings = {
        "accounts": [],
        "trades": [],
        "precision_ms": 20,
        "click_offset_ms": 0
    }
    save_settings()

def save_settings():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(current_settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ خطا در ذخیره تنظیمات: {e}")

# ──────────── وضعیت ربات ────────────
bot_running = False
current_stop_event = None
bot_lock = threading.Lock()

# ===================== API برای دریافت لاگ‌ها =====================
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
        name = f"حساب {len(current_settings['accounts'])+1}"
    if username and password:
        current_settings["accounts"].append({
            "name": name,
            "username": username,
            "password": password
        })
        save_settings()
        return jsonify({"status": "ok", "accounts": current_settings["accounts"]})
    return jsonify({"error": "نام کاربری و رمز عبور الزامی است"}), 400

@app.route("/api/accounts/<int:index>", methods=["DELETE"])
def delete_account(index):
    if 0 <= index < len(current_settings["accounts"]):
        del current_settings["accounts"][index]
        # به‌روزرسانی account_id معاملات
        for trade in current_settings["trades"]:
            if trade.get("account_id", 0) == index:
                trade["account_id"] = 0
            elif trade.get("account_id", 0) > index:
                trade["account_id"] -= 1
        save_settings()
        return jsonify({"status": "ok", "accounts": current_settings["accounts"]})
    return jsonify({"error": "ایندکس نامعتبر"}), 404

# ===================== مدیریت معاملات =====================
@app.route("/api/trades", methods=["POST"])
def add_trade():
    data = request.json
    symbol = data.get("symbol")
    trade_type = data.get("type")
    amount = data.get("amount")
    target_time = data.get("target_time")
    account_id = data.get("account_id", 0)

    if not all([symbol, trade_type, amount, target_time]):
        return jsonify({"error": "همه فیلدها الزامی است"}), 400
    try:
        amount = int(amount)
    except:
        return jsonify({"error": "مبلغ/تعداد باید عدد باشد"}), 400

    if trade_type not in ("خرید", "فروش"):
        return jsonify({"error": "نوع معامله نامعتبر"}), 400

    import re
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", target_time):
        return jsonify({"error": "فرمت ساعت باید HH:MM:SS باشد"}), 400

    new_trade = {
        "symbol": symbol,
        "order_type": trade_type,
        "target_time": target_time,
        "account_id": account_id,
        "invest_amount": amount if trade_type == "خرید" else None,
        "quantity": amount if trade_type == "فروش" else None,
        "amount": amount,
        "type": trade_type
    }
    current_settings["trades"].append(new_trade)
    save_settings()
    return jsonify({"status": "ok", "trades": current_settings["trades"]})

@app.route("/api/trades/<int:index>", methods=["PUT"])
def update_trade(index):
    data = request.json
    if not (0 <= index < len(current_settings["trades"])):
        return jsonify({"error": "ایندکس نامعتبر"}), 404

    symbol = data.get("symbol")
    trade_type = data.get("type")
    amount = data.get("amount")
    target_time = data.get("target_time")
    account_id = data.get("account_id")

    if not all([symbol, trade_type, amount, target_time]):
        return jsonify({"error": "همه فیلدها الزامی است"}), 400
    try:
        amount = int(amount)
    except:
        return jsonify({"error": "مبلغ/تعداد باید عدد باشد"}), 400

    if trade_type not in ("خرید", "فروش"):
        return jsonify({"error": "نوع معامله نامعتبر"}), 400

    import re
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", target_time):
        return jsonify({"error": "فرمت ساعت باید HH:MM:SS باشد"}), 400

    current_settings["trades"][index] = {
        "symbol": symbol,
        "order_type": trade_type,
        "target_time": target_time,
        "account_id": account_id,
        "invest_amount": amount if trade_type == "خرید" else None,
        "quantity": amount if trade_type == "فروش" else None,
        "amount": amount,
        "type": trade_type
    }
    save_settings()
    return jsonify({"status": "ok", "trades": current_settings["trades"]})

@app.route("/api/trades/<int:index>", methods=["DELETE"])
def delete_trade(index):
    if 0 <= index < len(current_settings["trades"]):
        del current_settings["trades"][index]
        save_settings()
        return jsonify({"status": "ok", "trades": current_settings["trades"]})
    return jsonify({"error": "ایندکس نامعتبر"}), 404

# ===================== تنظیمات دقت زمان ارسال =====================
@app.route("/api/precision", methods=["GET", "POST"])
def precision():
    if request.method == "POST":
        data = request.json
        try:
            val = int(data.get("precision_ms", 20))
            val = max(0, min(val, 1000))
            current_settings["precision_ms"] = val
            save_settings()
            return jsonify({"status": "ok", "precision_ms": val})
        except:
            return jsonify({"error": "مقدار نامعتبر"}), 400
    else:
        return jsonify({"precision_ms": current_settings["precision_ms"]})

# ===================== تنظیمات offset (کلیک زودتر) =====================
@app.route("/api/offset", methods=["GET", "POST"])
def offset():
    if request.method == "POST":
        data = request.json
        try:
            val = int(data.get("offset_ms", 0))
            val = max(0, val)   # فقط مثبت
            current_settings["click_offset_ms"] = val
            save_settings()
            return jsonify({"status": "ok", "offset_ms": val})
        except:
            return jsonify({"error": "مقدار نامعتبر"}), 400
    else:
        return jsonify({"offset_ms": current_settings.get("click_offset_ms", 0)})

# ===================== اجرا و توقف ربات =====================
@app.route("/api/run", methods=["POST"])
def run_bot():
    global bot_running, current_stop_event
    with bot_lock:
        if bot_running:
            return jsonify({"error": "ربات در حال اجراست. ابتدا آن را متوقف کنید."}), 400
        if not current_settings["accounts"]:
            return jsonify({"error": "حداقل یک حساب تعریف کنید"}), 400
        if not current_settings["trades"]:
            return jsonify({"error": "حداقل یک معامله تعریف کنید"}), 400

        accounts_snapshot = current_settings["accounts"].copy()
        trades_snapshot = current_settings["trades"].copy()
        precision_ms = current_settings.get("precision_ms", 20)
        click_offset_ms = current_settings.get("click_offset_ms", 0)
        stop_event = asyncio.Event()
        current_stop_event = stop_event

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    run_multiple_trades(accounts_snapshot, trades_snapshot, precision_ms, stop_event, click_offset_ms)
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
        return jsonify({"status": "درخواست توقف ربات ارسال شد. مرورگرها بسته خواهند شد."})

# ===================== دریافت کل تنظیمات =====================
@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(current_settings)

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
    load_settings()  # بارگذاری تنظیمات از فایل (در صورت وجود)
    app.run(debug=True, host="0.0.0.0", port=3000)