# bot_core.py
import asyncio
import re
import contextvars
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
import os
from dotenv import load_dotenv
# ──────────── Constants ────────────
LOGIN_URL = "https://login.emofid.com/Login"
TRADE_URL = "https://d.easytrader.ir"
TIMEOUT_SHORT = 5000   # ms
TIMEOUT_MED = 10000    # ms
TIMEOUT_LONG = 20000   # ms
BLOCKED_RESOURCES = re.compile(r"\.(png|jpe?g|webp|svg|gif|ico|ttf|woff2?|eot|mp4|mp3|pdf)(\?|$)", re.I)

# ──────────── Portfolio Selectors ────────────
PORTFOLIO_MENU_SELECTOR = "[data-cy='portfolio-menu-icon']"
PORTFOLIO_GRID_SELECTOR = "[data-cy='portfolio_grid']"

# ──────────── Log Buffer ────────────
log_buffer = []
MAX_LOG_ENTRIES = 500

# ──────────── Context for Logging ────────────
_log_account_ctx = contextvars.ContextVar("log_account", default="")
_log_symbol_ctx = contextvars.ContextVar("log_symbol", default="")

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    account = _log_account_ctx.get()
    symbol = _log_symbol_ctx.get()
    prefix = ""
    if account:
        prefix += f"[{account}]"
    if symbol:
        prefix += f"[{symbol}]"
    if prefix:
        formatted = f"[{ts}] {prefix} {msg}"
    else:
        formatted = f"[{ts}] {msg}"
    print(formatted, flush=True)
    log_buffer.append({
        "time": ts,
        "account": account,
        "symbol": symbol,
        "message": msg
    })
    if len(log_buffer) > MAX_LOG_ENTRIES:
        log_buffer.pop(0)

# ──────────── Helper: Reliable Input Filling ────────────
async def _fill_input_reliable(locator, value: str) -> None:
    await locator.wait_for(state="visible", timeout=TIMEOUT_SHORT)
    await locator.click(click_count=3)
    await locator.type(str(value), delay=20)

# ──────────── Notification Logger ────────────
async def log_notification(symbol: str, message: str, account_name: str = "") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if account_name:
        log_entry = f"[{timestamp}] [{account_name}] [{symbol}] {message}\n"
    else:
        log_entry = f"[{timestamp}] [{symbol}] {message}\n"
    try:
        with open("notifications.log", "a", encoding="utf-8") as f:
            f.write(log_entry)
        log(f"📢 [{account_name}] [{symbol}] نوتیف: {message}")
    except Exception as e:
        log(f"⚠️ خطا در نوشتن نوتیف: {e}")

async def setup_notification_monitor(page, symbol: str, account_name: str) -> None:
    async def callback(msg):
        await log_notification(symbol, msg, account_name)
    await page.expose_function("logNotification", callback)

    observer_script = """
    (function() {
        function observeContainer(container) {
            const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    mutation.addedNodes.forEach((node) => {
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            if (node.matches && node.matches('[data-cy="notify-class"]')) {
                                const msgSpan = node.querySelector('[data-cy="notify-message"]');
                                if (msgSpan && msgSpan.innerText) {
                                    window.logNotification(msgSpan.innerText);
                                }
                            }
                            if (node.querySelectorAll) {
                                const items = node.querySelectorAll('[data-cy="notify-class"]');
                                items.forEach(item => {
                                    const msgSpan = item.querySelector('[data-cy="notify-message"]');
                                    if (msgSpan && msgSpan.innerText) {
                                        window.logNotification(msgSpan.innerText);
                                    }
                                });
                            }
                        }
                    });
                });
            });
            observer.observe(container, { childList: true, subtree: true });
        }
        let targetNode = document.querySelector('.notify-desktop');
        if (targetNode) {
            observeContainer(targetNode);
        } else {
            const waitObserver = new MutationObserver((mutations, obs) => {
                const node = document.querySelector('.notify-desktop');
                if (node) {
                    obs.disconnect();
                    observeContainer(node);
                }
            });
            waitObserver.observe(document.body, { childList: true, subtree: true });
        }
        const existing = document.querySelectorAll('[data-cy="notify-class"]');
        existing.forEach(item => {
            const msgSpan = item.querySelector('[data-cy="notify-message"]');
            if (msgSpan && msgSpan.innerText) {
                window.logNotification(msgSpan.innerText);
            }
        });
    })();
    """
    await page.evaluate(observer_script)
    log(f"✅ مانیتور نوتیفیکیشن برای {symbol} (حساب: {account_name}) راه‌اندازی شد")

# ──────────── Block Resources ────────────
async def _block_route(route):
    if BLOCKED_RESOURCES.search(route.request.url):
        await route.abort()
    else:
        await route.continue_()

# ──────────── Helper: Wait for page fully loaded ────────────
async def wait_for_page_ready(page, timeout: int = 30000) -> None:
    log("⏳ منتظر بارگذاری کامل صفحه...")
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PWTimeout:
        log("⚠️ networkidle timeout, continuing...")

    for attempt in range(3):
        try:
            await page.wait_for_selector("[data-cy='market-watch-grid']",
                                         state="visible",
                                         timeout=timeout)
            log("✅ صفحه آماده است (market-watch-grid قابل مشاهده)")
            return
        except PWTimeout:
            if attempt < 2:
                log(f"⚠️ تلاش {attempt+1}: market-watch-grid پیدا نشد، رفرش می‌کنم...")
                await page.reload()
                await page.wait_for_load_state("networkidle", timeout=timeout)
            else:
                raise RuntimeError("پس از چند بار تلاش، market-watch-grid ظاهر نشد.")

# ──────────── Login and Redirect ────────────
async def login_and_redirect(page, username: str, password: str) -> None:
    log("⏳ ورود به صفحه لاگین...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUT_LONG)
    await page.wait_for_selector("#user-name", state="visible", timeout=TIMEOUT_MED)
    await page.fill("#user-name", username)
    await page.fill("#password", password)
    await page.click("button[type='submit']")
    log("✓ اطلاعات ارسال شد — منتظر صفحه بعد از لاگین...")

    try:
        login_btn = page.get_by_role("link", name="ورود", exact=True)
        await login_btn.wait_for(state="visible", timeout=TIMEOUT_LONG)
        log("✓ دکمه ورود به صرافی پیدا شد — کلیک...")
        await login_btn.click()
    except PWTimeout:
        log("⚠️ دکمه ورود پیدا نشد — تلاش برای ریدایرکت مستقیم...")

    try:
        await page.wait_for_url(
            lambda url: "easytrader.ir" in url,
            timeout=TIMEOUT_LONG * 2
        )
        log(f"✓ ریدایرکت نهایی: {page.url}")
    except PWTimeout:
        log("⚠️ زمان انتظار برای ریدایرکت به easytrader.ir تمام شد، انتقال مستقیم...")
        await page.goto(TRADE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_LONG)

    if "easytrader.ir" not in page.url:
        log("🔄 انتقال به صفحه معاملات...")
        await page.goto(TRADE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_LONG)

    await wait_for_page_ready(page)
    log("✅ صفحه معاملات بارگذاری شد")

# ──────────── Select Symbol ────────────
async def select_symbol(page, symbol_name: str) -> None:
    log(f"🔍 جستجوی نماد «{symbol_name}»...")
    await page.wait_for_selector("[data-cy='market-watch-grid']", state="visible", timeout=TIMEOUT_MED)

    symbol_cell = page.locator(f"[data-cy='renderer-symbol-name']:has-text('{symbol_name}')")
    if await symbol_cell.count() == 0:
        symbol_cell = page.locator(f"div[data-cy='renderer-symbol-name']:has-text('{symbol_name}')")

    if await symbol_cell.count() == 0:
        rows = page.locator("[role='row']").filter(has_text=symbol_name)
        if await rows.count() == 0:
            raise RuntimeError(f"نماد «{symbol_name}» در دیده‌بان یافت نشد.")
        target_row = rows.first
    else:
        target_row = symbol_cell.first.locator("xpath=ancestor::div[@role='row']")

    link = target_row.locator("a").first
    if await link.count():
        await link.click()
    else:
        await target_row.click()

    log(f"✅ روی نماد «{symbol_name}» کلیک شد")
    await page.wait_for_selector("lib-symbol-header", state="visible", timeout=TIMEOUT_MED)
    await page.wait_for_selector("[data-cy='order-buy-btn'], [data-cy='order-sell-btn']", state="visible",
                                 timeout=TIMEOUT_SHORT)
    log("✅ جزئیات سهم نمایش داده شد")
    await asyncio.sleep(1)

# ──────────── Click Order Button ────────────
async def click_order_button(page, order_type: str) -> None:
    if order_type == "خرید":
        log("🟢 کلیک دکمه خرید...")
        btn = page.locator("[data-cy='order-buy-btn']")
    else:
        log("🔴 کلیک دکمه فروش...")
        btn = page.locator("[data-cy='order-sell-btn']")
    await btn.wait_for(state="visible", timeout=TIMEOUT_SHORT)
    await btn.click()
    await page.wait_for_selector("[data-cy='order-form-input-price']",
                                 state="visible", timeout=TIMEOUT_MED)
    log("✅ فرم سفارش باز شد")

# ──────────── Get Threshold Price ────────────
async def get_threshold_price(page, order_type: str) -> int:
    log("💰 دریافت قیمت آستانه فردا...")
    await page.wait_for_selector("lib-symbol-info", state="visible", timeout=TIMEOUT_MED)

    price_span = page.locator(
        "//span[contains(@class,'text-gray-600') and contains(text(),'آستانه فردا')]/following-sibling::span[@dir='ltr']"
    )

    try:
        await price_span.wait_for(state="attached", timeout=2000)
        range_text = await price_span.inner_text()
        log(f"متن قیمت یافت شد: '{range_text}'")
    except PWTimeout:
        log("⚠️ روش مستقیم ناموفق، استفاده از regex روی کل متن...")
        info_text = await page.locator("lib-symbol-info").inner_text()
        m = re.search(r"آستانه\s+فردا\s+([\d,]+)\s*[-–]\s*([\d,]+)", info_text)
        if m:
            lower = int(m.group(1).replace(",", ""))
            upper = int(m.group(2).replace(",", ""))
            return upper if order_type == "خرید" else lower
        m = re.search(r"آستانه\s+فردا\s+([\d,]+)\s+([\d,]+)", info_text)
        if m:
            lower = int(m.group(1).replace(",", ""))
            upper = int(m.group(2).replace(",", ""))
            return upper if order_type == "خرید" else lower
        m = re.search(r"آستانه\s+فردا\s+([\d,]+)", info_text)
        if m:
            single = int(m.group(1).replace(",", ""))
            log(f"⚠️ فقط یک عدد یافت شد: {single}")
            return single
        log("=== متن lib-symbol-info ===\n" + info_text + "\n===========================")
        raise RuntimeError("محدوده آستانه فردا یافت نشد.")

    numbers = re.findall(r"[\d,]+", range_text)
    if len(numbers) >= 2:
        lower = int(numbers[0].replace(",", ""))
        upper = int(numbers[1].replace(",", ""))
        log(f"✓ محدوده: {lower:,} - {upper:,}")
        return upper if order_type == "خرید" else lower
    elif len(numbers) == 1:
        single = int(numbers[0].replace(",", ""))
        log(f"⚠️ فقط یک عدد یافت شد: {single}")
        return single
    else:
        raise RuntimeError(f"فرمت متن قیمت نامعتبر: '{range_text}'")

# ──────────── Fill Order Form ────────────
async def fill_order_form(page, order_type: str, invest_amount: int | None, quantity: int | None) -> tuple[int, int]:
    price = await get_threshold_price(page, order_type)
    log(f"✓ قیمت {'خرید' if order_type == 'خرید' else 'فروش'}: {price:,} تومان")

    price_input = page.locator("[data-cy='order-form-input-price']")
    await price_input.wait_for(state="visible", timeout=TIMEOUT_SHORT)
    await price_input.click(click_count=3)
    await price_input.fill(str(price))

    try:
        await page.locator(".popover:has-text('خطا')").wait_for(state="hidden", timeout=2000)
    except:
        pass

    if order_type == "خرید":
        if invest_amount is None:
            raise RuntimeError("برای خرید نیاز به مبلغ سرمایه‌گذاری است")
        qty = invest_amount // price
        if qty < 1:
            raise RuntimeError(f"مبلغ {invest_amount:,} از قیمت یک سهم {price:,} کمتر است.")
        log(f"🧮 محاسبه تعداد سهم: {invest_amount:,} ÷ {price:,} = {qty} سهم")
    else:
        if quantity is None:
            raise RuntimeError("برای فروش نیاز به تعداد سهم است")
        qty = quantity
        log(f"📦 تعداد سهم از تنظیمات: {qty} سهم")

    qty_input = page.locator("[data-cy='order-form-input-quantity']")
    await qty_input.click(force=True)
    await qty_input.fill(str(qty))

    log(f"✅ فرم سفارش پر شد: قیمت={price:,}  تعداد={qty}")
    return price, qty

# ──────────── Wait and Submit at Exact Time ────────────
async def wait_and_submit_at_time(page, order_type: str, target_time_str: str, precision_ms: int = 20, click_offset_ms: int = 0) -> None:
    now = datetime.now()
    h, m, s = map(int, target_time_str.split(':'))
    target_dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
    if target_dt <= now:
        target_dt += timedelta(days=1)
    target_ts = target_dt.timestamp() - (click_offset_ms / 1000.0)
    log(f"⏰ زمان هدف اصلی: {target_dt.strftime('%H:%M:%S')} | کلیک در: {datetime.fromtimestamp(target_ts).strftime('%H:%M:%S.%f')[:-3]} (با offset {click_offset_ms}ms)")

    remaining = target_ts - datetime.now().timestamp()
    offset_sec = precision_ms / 1000.0
    if remaining > 0:
        sleep_duration = max(0, remaining - offset_sec)
        if sleep_duration > 0:
            await asyncio.sleep(sleep_duration)
        while datetime.now().timestamp() < target_ts:
            await asyncio.sleep(0.001)

    click_ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    log(f"🎯 کلیک در {click_ts}")

    if order_type == "خرید":
        submit = page.locator("[data-cy='oms-order-form-submit-button-buy']")
    else:
        submit = page.locator(
            "[data-cy='oms-order-form-submit-button-sell'], "
            "button:has-text('ارسال فروش'), "
            "button.btn-success:has-text('فروش')"
        ).first
    await submit.wait_for(state="visible", timeout=TIMEOUT_SHORT)
    await submit.click()
    log("✅ سفارش ارسال شد")

# ──────────── Navigate To Portfolio ────────────
async def navigate_to_portfolio(page) -> None:
    log("📊 رفتن به بخش پرتفوی...")
    btn = page.locator(PORTFOLIO_MENU_SELECTOR)
    await btn.wait_for(state="visible", timeout=TIMEOUT_MED)
    await btn.click()
    log("✓ روی منوی پرتفوی کلیک شد")

    try:
        await page.wait_for_selector(f"{PORTFOLIO_GRID_SELECTOR} div[role='row'][row-id]", timeout=TIMEOUT_LONG)
    except PWTimeout:
        log("⚠️ ردیفی در پرتفوی پیدا نشد (احتمالاً پرتفوی خالی است)")
    await asyncio.sleep(1)
    log("✅ صفحه پرتفوی بارگذاری شد")

# ──────────── Extract Portfolio ────────────
async def extract_portfolio(page) -> list[dict]:
    log("📥 استخراج اطلاعات پرتفوی...")
    rows = page.locator(f"{PORTFOLIO_GRID_SELECTOR} div[role='row'][row-id]")
    count = await rows.count()
    results = []

    for i in range(count):
        row = rows.nth(i)
        try:
            symbol_el = row.locator("[data-cy='renderer-symbol-name']").first
            if await symbol_el.count() == 0:
                continue
            symbol = (await symbol_el.inner_text()).strip()
            if not symbol:
                continue

            asset_el = row.locator("[data-cy='portfolio-symbol-asset-renderer-asset']").first
            quantity = (await asset_el.inner_text()).strip() if await asset_el.count() else ""

            current_value = ""
            current_value_cell = row.locator("[col-id='currentValue']")
            if await current_value_cell.count():
                v = current_value_cell.locator("div.text-start").first
                if await v.count():
                    current_value = (await v.inner_text()).strip()

            last_price, last_price_percent = "", ""
            last_price_cell = row.locator("[col-id='lastTradedPrice']")
            if await last_price_cell.count():
                p = last_price_cell.locator("div.text-end").first
                if await p.count():
                    last_price_percent = (await p.inner_text()).strip()
                v = last_price_cell.locator("div.text-start").first
                if await v.count():
                    last_price = (await v.inner_text()).strip()

            today_profit_percent, today_profit_value = "", ""
            today_profit_cell = row.locator("[col-id='todayProfitAmount']")
            if await today_profit_cell.count():
                p = today_profit_cell.locator("div.text-end").first
                if await p.count():
                    today_profit_percent = (await p.inner_text()).strip()
                v = today_profit_cell.locator("div.text-start").first
                if await v.count():
                    today_profit_value = (await v.inner_text()).strip()

            results.append({
                "symbol": symbol,
                "quantity": quantity,
                "current_value": current_value,
                "last_price": last_price,
                "last_price_percent": last_price_percent,
                "today_profit_percent": today_profit_percent,
                "today_profit_value": today_profit_value,
            })
        except Exception as e:
            log(f"⚠️ خطا در استخراج ردیف پرتفوی #{i}: {e}")

    log(f"✅ {len(results)} سهم از پرتفوی استخراج شد")
    return results

# ──────────── Sync Portfolio For One Account ────────────
async def sync_account_portfolio(username: str, password: str, account_name: str) -> list[dict]:
    token_account = _log_account_ctx.set(account_name)
    holdings: list[dict] = []
    try:
        log(f"🚀 شروع همگام‌سازی پرتفوی (حساب: {account_name})")
        async with async_playwright() as pw:
            headless_mode = os.getenv("HEADLESS", "false").lower() == "true"
            browser = await pw.firefox.launch(
                headless=headless_mode,
                args=[
                    "--start-maximized",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-extensions",
                    "--disable-sync",
                    "--no-first-run",
                ],
            )
            context = await browser.new_context(
                viewport=None,
                ignore_https_errors=True,
                java_script_enabled=True,
            )
            await context.route("**/*", _block_route)
            page = await context.new_page()
            try:
                await login_and_redirect(page, username, password)
                await navigate_to_portfolio(page)
                holdings = await extract_portfolio(page)
            except Exception as e:
                log(f"❌ خطا در همگام‌سازی پرتفوی ({account_name}): {e}")
            finally:
                await browser.close()
    finally:
        _log_account_ctx.reset(token_account)
    return holdings

# ──────────── Sync Portfolio For All Accounts ────────────
async def run_portfolio_sync(accounts: list) -> dict:
    """برای هر حساب پرتفوی را استخراج می‌کند و دیکشنری {account_id: holdings} برمی‌گرداند"""
    tasks = [
        sync_account_portfolio(acc["username"], acc["password"], acc.get("name", f"حساب {i+1}"))
        for i, acc in enumerate(accounts)
    ]
    holdings_list = await asyncio.gather(*tasks, return_exceptions=True)

    results = {}
    for acc, holdings in zip(accounts, holdings_list):
        if isinstance(holdings, Exception):
            log(f"❌ خطا در دریافت پرتفوی حساب {acc.get('name')}: {holdings}")
            holdings = []
        results[acc["id"]] = holdings
    return results

# ──────────── Single Trade Runner ────────────
async def run_single_trade(
    trade: dict,
    username: str,
    password: str,
    account_name: str,
    trade_index: int,
    stop_event: asyncio.Event,
    precision_ms: int = 20,
    click_offset_ms: int = 0,
) -> None:
    token_account = _log_account_ctx.set(account_name)
    token_symbol = _log_symbol_ctx.set(trade["symbol"])
    try:
        log(f"🚀 شروع معامله {trade_index+1}: {trade['symbol']} - {trade['order_type']} (حساب: {account_name})")
        async with async_playwright() as pw:
            headless_mode = os.getenv("HEADLESS", "false").lower() == "true"
            browser = await pw.firefox.launch(
                headless=headless_mode,
                args=[
                    "--start-maximized",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-extensions",
                    "--disable-sync",
                    "--no-first-run",
                ],
            )
            context = await browser.new_context(
                viewport=None,
                ignore_https_errors=True,
                java_script_enabled=True,
            )
            await context.route("**/*", _block_route)
            page = await context.new_page()

            try:
                await login_and_redirect(page, username, password)
                await setup_notification_monitor(page, trade["symbol"], account_name)
                await select_symbol(page, trade["symbol"])
                await click_order_button(page, trade["order_type"])
                await fill_order_form(page, trade["order_type"], trade.get("invest_amount"), trade.get("quantity"))
                await wait_and_submit_at_time(page, trade["order_type"], trade["target_time"], precision_ms, click_offset_ms)

                log(f"✅ معامله {trade['symbol']} تکمیل شد. در انتظار فرمان توقف...")
                await stop_event.wait()
                log(f"🛑 معامله {trade['symbol']} متوقف شد. بستن مرورگر...")
            except Exception as e:
                log(f"❌ خطا در معامله {trade['symbol']}: {e}")
    finally:
        # بازگرداندن context به حالت قبل (پاک کردن)
        _log_account_ctx.reset(token_account)
        _log_symbol_ctx.reset(token_symbol)

# ──────────── Main Orchestrator ────────────
async def run_multiple_trades(accounts: list, trades: list, precision_ms: int = 20, stop_event: asyncio.Event = None, click_offset_ms: int = 0):
    if stop_event is None:
        stop_event = asyncio.Event()

    try:
        with open("notifications.log", "w", encoding="utf-8") as f:
            pass
        log("🗑️ فایل notifications.log پاک شد (شروع جدید)")
    except Exception as e:
        log(f"⚠️ خطا در پاک کردن فایل لاگ: {e}")

    tasks = []
    for idx, trade in enumerate(trades):
        acc_id = trade.get("account_id", 0)
        if acc_id >= len(accounts):
            log(f"⚠️ account_id {acc_id} نامعتبر است، استفاده از حساب اول")
            acc_id = 0
        account = accounts[acc_id]
        username = account["username"]
        password = account["password"]
        account_name = account.get("name", f"حساب {acc_id+1}")
        task = asyncio.create_task(
            run_single_trade(trade, username, password, account_name, idx, stop_event, precision_ms, click_offset_ms)
        )
        tasks.append(task)

    await asyncio.gather(*tasks)