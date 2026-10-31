import os
import json
import time
import requests
import pandas as pd

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# تنظیمات پیشرفته مدیریت سرمایه و سود حداکثری
MAX_COINS_TO_SCAN = 60          # تعداد ارزهای برتر بازار برای اسکن در هر نوبت
TRADE_AMOUNT = 20.0             # مبلغ ورود به هر معامله (۲۰ تتر)
RSI_BUY_THRESHOLD = 30.0        # خرید فقط در کف‌های بسیار جذاب (RSI زیر ۳۰)
MIN_PROFIT_TO_TRAIL = 3.0       # شروع تعقیب سود از ۳ درصد به بالا
TRAILING_DROP_PERCENT = 1.0     # فروش در صورتی که قیمت ۱ درصد از بالاترین قله عقب‌نشینی کرد
STOP_LOSS_PERCENT = -2.5        # حد ضرر برای محافظت از سرمایه

PORTFOLIO_FILE = "portfolio.json"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"خطا در ارسال تلگرام: {e}")

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    
    initial_data = {"balance": 100.0, "positions": {}}
    send_telegram(
        "⭕️ ربات اسکنر پیشرفته نوبیتکس روشن شد!\n\n"
        "✅ موجودی اولیه مجازی: ۱۰۰ تتر\n"
        "✅ پوشش بازار: اسکن خودکار ۶۰ ارز پرحجم بازار\n"
        "✅ سیستم فروش: قفل سود متحرک (Trailing Profit) برای شکار سودهای حداکثری\n\n"
        "🛑 ربات در حال رصد لحظه‌ای کل بازار است..."
    )
    return initial_data

def save_portfolio(data):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_top_usdt_markets(limit=60):
    """دریافت لیست ۶۰ ارز پرحجم بازار نوبیتکس بر پایه تتر"""
    url = "https://api.nobitex.ir/market/stats"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        if res.get("status") == "ok":
            stats = res.get("stats", {})
            usdt_pairs = []
            for pair, data in stats.items():
                if pair.endswith("-usdt") and not data.get("isClosed", False):
                    # تبدیل فرمت btc-usdt به BTCUSDT برای نمودار
                    symbol = pair.replace("-", "").upper()
                    volume = float(data.get("volumeDst", 0))
                    usdt_pairs.append((symbol, volume))
            
            # مرتب‌سازی بر اساس بیشترین حجم معاملات تا در ارزهای بی‌کیفیت گیر نکنیم
            usdt_pairs.sort(key=lambda x: x[1], reverse=True)
            return [item[0] for item in usdt_pairs[:limit]]
    except Exception as e:
        print(f"خطا در دریافت لیست بازارها: {e}")
    
    # لیست پشتیبان در صورت اختلال لحظه‌ای در اینترنت
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TONUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "SHIBUSDT", "TRXUSDT"]

def get_nobitex_candles(symbol):
    to_time = int(time.time())
    from_time = to_time - (50 * 15 * 60)  # ۵۰ کندل ۱۵ دقیقه‌ای
    url = f"https://api.nobitex.ir/market/udf/history?symbol={symbol}&resolution=15&from={from_time}&to={to_time}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        if res.get("s") == "ok" and len(res.get("c", [])) >= 20:
            return pd.Series(res["c"], dtype=float)
    except Exception:
        pass
    return None

def calculate_rsi(prices, period=14):
    """محاسبه دقیق شاخص RSI"""
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

def run_bot():
    portfolio = load_portfolio()
    positions = portfolio["positions"]
    
    top_symbols = get_top_usdt_markets(MAX_COINS_TO_SCAN)
    # اطمینان از اینکه ارزهای خریداری شده حتماً در لیست بررسی قرار دارند
    all_symbols = list(dict.fromkeys(list(positions.keys()) + top_symbols))
    
    print(f"شروع اسکن {len(all_symbols)} ارز در نوبیتکس...")
    buy_candidates = []
    
    for symbol in all_symbols:
        prices = get_nobitex_candles(symbol)
        if prices is None:
            time.sleep(0.2)
            continue
            
        current_price = float(prices.iloc[-1])
        rsi = calculate_rsi(prices)
        
        # ۱. بررسی وضعیت ارزهایی که از قبل خریده‌ایم
        if symbol in positions:
            pos = positions[symbol]
            buy_price = pos["buy_price"]
            amount = pos["amount"]
            highest_price = max(pos.get("highest_price", buy_price), current_price)
            pos["highest_price"] = highest_price
            
            current_profit_pct = ((current_price - buy_price) / buy_price) * 100
            max_profit_pct = ((highest_price - buy_price) / buy_price) * 100
            drop_from_peak_pct = ((highest_price - current_price) / highest_price) * 100
            
            should_sell = False
            sell_reason = ""
            
            # الف) سیستم قفل سود متحرک: اگر سود از ۳٪ رد شده بود و حالا ۱٪ از قله برگشته یا RSI بالای ۷۵ رفته
            if max_profit_pct >= MIN_PROFIT_TO_TRAIL and (drop_from_peak_pct >= TRAILING_DROP_PERCENT or rsi > 75):
                should_sell = True
                sell_reason = f"قفل سود متحرک (سقف لمس شده: {max_profit_pct:.1f}%)"
            # ب) حد ضرر برای جلوگیری از قفل شدن سرمایه
            elif current_profit_pct <= STOP_LOSS_PERCENT:
                should_sell = True
                sell_reason = "حد ضرر (محافظت از سرمایه)"
                
            if should_sell:
                sell_value = amount * current_price
                profit_usdt = sell_value - (amount * buy_price)
                portfolio["balance"] += sell_value
                del portfolio["positions"][symbol]
                
                msg = (
                    f"⭕️ گزارش فروش هوشمند\n\n"
                    f"✅ ارز: {symbol}\n"
                    f"✅ علت فروش: {sell_reason}\n"
                    f"✅ قیمت خرید: {buy_price} | فروش: {current_price}\n"
                    f"✅ سود/زیان خالص: {current_profit_pct:.2f}% ({profit_usdt:.2f} تتر)\n"
                    f"✅ موجودی کل فعلی: {portfolio['balance']:.2f} تتر"
                )
                send_telegram(msg)
                
        # ۲. پیدا کردن بهترین فرصت‌های خرید جدید
        else:
            # شرط خرید: RSI در کف باشد و قیمت آخرین کندل نسبت به کندل قبلی شروع به برگشت رو به بالا کرده باشد
            if rsi <= RSI_BUY_THRESHOLD and current_price >= float(prices.iloc[-2]):
                buy_candidates.append({
                    "symbol": symbol,
                    "price": current_price,
                    "rsi": rsi
                })
                
        # مکث کوتاه (یک‌چهارم ثانیه) برای جلوگیری از مسدود شدن توسط فایروال نوبیتکس
        time.sleep(0.25)

    # ۳. مرتب‌سازی فرصت‌های خرید (اولین خرید برای ارزی که در عمیق‌ترین کف قیمتی است)
    buy_candidates.sort(key=lambda x: x["rsi"])
    
    for candidate in buy_candidates:
        if portfolio["balance"] >= TRADE_AMOUNT:
            sym = candidate["symbol"]
            price = candidate["price"]
            rsi_val = candidate["rsi"]
            
            amount = TRADE_AMOUNT / price
            portfolio["balance"] -= TRADE_AMOUNT
            portfolio["positions"][sym] = {
                "buy_price": price,
                "amount": amount,
                "highest_price": price
            }
            
            msg = (
                f"⭕️ شکار فرصت خرید جدید (از بین {len(all_symbols)} ارز)\n\n"
                f"✅ ارز: {sym}\n"
                f"✅ قیمت ورود: {price} تتر\n"
                f"✅ شاخص RSI: {rsi_val:.1f} (کف قیمتی و شروع برگشت)\n"
                f"✅ مبلغ ورود: {TRADE_AMOUNT} تتر\n"
                f"✅ موجودی نقد باقی‌مانده: {portfolio['balance']:.2f} تتر"
            )
            send_telegram(msg)
        else:
            break

    save_portfolio(portfolio)

if __name__ == "__main__":
    run_bot()
