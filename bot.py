import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# خواندن اطلاعات امنیتی از گاوصندوق گیت‌هاب
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ==================== تنظیمات استراتژی بهینه (۲۵ مرحله تست‌شده) ====================
MAX_COINS_TO_SCAN = 60          # بررسی ۶۰ ارز برتر بازار نوبیتکس
TRADE_AMOUNT = 20.0             # مبلغ ورود در هر پله خرید (۲۰ تتر)
MAX_BUYS_PER_RUN = 2            # حداکثر خرید در هر چرخه ۱۵ دقیقه‌ای
MAX_OPEN_POSITIONS = 5          # حداکثر تعداد معاملات باز همزمان
FEE_PERCENT = 0.35              # مجموع کارمزد رفت و برگشت صرافی (درصد)
MAX_SPREAD_PERCENT = 0.75       # حداکثر شکاف مجاز بین قیمت خرید و فروش در تابلو (درصد)

RSI_BUY_LIMIT = 32.0            # سقف RSI برای شکار کف قیمتی
BREAKEVEN_TRIGGER = 1.5         # فعال‌سازی سپر بدون ریسک در ۱.۵٪ سود ناخالص
BREAKEVEN_FLOOR = 0.55          # خروج با سود خالص در صورت برگشت قیمت پس از ریسک‌فری
TRAILING_START = 2.4            # شروع تعقیب سود از ۲.۴٪ به بالا
STOP_LOSS_LIMIT = -2.0          # حد ضرر قطعی برای محافظت از سرمایه
COOLDOWN_SECONDS = 4 * 3600     # ۴ ساعت جریمه و ممنوعیت خرید برای ارز حد ضرر خورده
STAGNANT_HOURS = 12             # آزادسازی سرمایه راکد پس از ۱۲ ساعت در صورت درجا زدن

# لیست سیاه استیبل‌کوین‌ها و توکن‌های ثابت
BLACKLIST_SYMBOLS = {
    "DAIUSDT", "USDCUSDT", "BUSDUSDT", "TUSDUSDT", 
    "USDPUSDT", "USDTUSDT", "FDUSDUSDT", "PYUSDUSDT", "PAXGUSDT", "XAUTUSDT"
}

PORTFOLIO_FILE = "portfolio.json"

def create_resilient_session():
    """ساخت نشست پایدار با سیستم تلاش مجدد خودکار در برابر خطاهای لحظه‌ای شبکه"""
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return session

SESSION = create_resilient_session()

def format_price(price):
    """نمایش استاندارد و خوانا برای انواع قیمت‌ها"""
    if price >= 10:
        return f"{price:,.2f}"
    elif price >= 0.1:
        return f"{price:.4f}"
    else:
        return f"{price:.8f}".rstrip("0").rstrip(".")

def is_silent_hours():
    """ارسال بی‌صدا در ساعات پایانی شب (۲۲:۴۵ تا ۰۷:۴۵ به وقت تهران)"""
    try:
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        current_minutes = now.hour * 60 + now.minute
        return current_minutes >= 1365 or current_minutes <= 465
    except Exception:
        return False

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_notification": is_silent_hours()
    }
    try:
        SESSION.post(url, json=payload, timeout=12)
    except Exception as e:
        print(f"خطا در ارسال تلگرام: {e}")

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "balance" in data and "positions" in data:
                    data.setdefault("cooldowns", {})
                    data.setdefault("total_trades", 0)
                    data.setdefault("win_trades", 0)
                    return data
        except Exception as e:
            print(f"خطا در خواندن حافظه، بازسازی ایمن: {e}")
    
    initial_data = {
        "balance": 100.0,
        "total_trades": 0,
        "win_trades": 0,
        "positions": {},
        "cooldowns": {}
    }
    send_telegram(
        "⭕️ ربات هوشمند نوبیتکس (نسخه نهایی تست‌شده در ۲۵ مرحله) فعال شد!\n\n"
        "✅ موجودی اولیه مجازی: ۱۰۰ تتر\n"
        "✅ اسکن هوشمند: بررسی ۶۰ ارز با فیلتر نقدینگی و شکاف قیمت (Spread)\n"
        "✅ مدیریت سرمایه: سپر بیت‌کوین + ریسک‌فری داینامیک + ضد ارز راکد\n\n"
        "🛑 ربات به صورت ۲۴ ساعته بازار را رصد می‌کند."
    )
    return initial_data

def save_portfolio(data):
    """ذخیره‌سازی اتمی (Atomic Write) جهت محافظت ۱۰۰ درصدی از فایل حافظه"""
    temp_file = f"{PORTFOLIO_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, PORTFOLIO_FILE)
    except Exception as e:
        print(f"خطا در ذخیره فایل حافظه: {e}")

def get_top_usdt_markets(limit=60):
    """دریافت لیست ۶۰ ارز پرحجم با اعتبارسنجی شکاف تابلو (Spread)"""
    url = "https://api.nobitex.ir/market/stats"
    market_info = {}
    try:
        res = SESSION.get(url, timeout=15).json()
        if res.get("status") == "ok":
            stats = res.get("stats", {})
            usdt_pairs = []
            for pair, data in stats.items():
                if pair.endswith("-usdt") and not data.get("isClosed", False):
                    symbol = pair.replace("-", "").upper()
                    if symbol in BLACKLIST_SYMBOLS:
                        continue
                    volume = float(data.get("volumeDst", 0))
                    best_buy = float(data.get("bestBuy", 0))
                    best_sell = float(data.get("bestSell", 0))
                    
                    spread_pct = ((best_sell - best_buy) / best_buy * 100) if best_buy > 0 else 99.0
                    market_info[symbol] = {"spread": spread_pct, "volume": volume}
                    
                    if volume >= 3000:
                        usdt_pairs.append((symbol, volume))
            
            usdt_pairs.sort(key=lambda x: x[1], reverse=True)
            top_list = [item[0] for item in usdt_pairs[:limit]]
            return top_list, market_info
    except Exception as e:
        print(f"خطا در دریافت لیست بازارها: {e}")
    
    fallback = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TONUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "SHIBUSDT", "TRXUSDT"]
    return fallback, market_info

def get_nobitex_candles(symbol):
    """دریافت کندل‌های ۱۵ دقیقه‌ای استاندارد برای تحلیل دقیق تکنیکال"""
    to_time = int(time.time())
    from_time = to_time - (120 * 15 * 60)
    url = f"https://api.nobitex.ir/market/udf/history?symbol={symbol}&resolution=15&from={from_time}&to={to_time}"
    try:
        res = SESSION.get(url, timeout=10).json()
        if res.get("s") == "ok" and len(res.get("c", [])) >= 30:
            return pd.DataFrame({
                "close": pd.Series(res["c"], dtype=float),
                "high": pd.Series(res["h"], dtype=float),
                "low": pd.Series(res["l"], dtype=float),
                "volume": pd.Series(res.get("v", [1]*len(res["c"])), dtype=float)
            })
    except Exception:
        pass
    return None

def calculate_indicators(df, period=14):
    """محاسبه دقیق RSI استاندارد Wilder و سنجش نوسان ATR"""
    closes = df["close"]
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = avg_loss.replace(0, 1e-10)
    
    rs = avg_gain / avg_loss
    rsi = (100 - (100 / (1 + rs))).fillna(50.0)
    
    high_low = df["high"] - df["low"]
    atr_pct = (high_low / closes * 100).rolling(window=14).mean().fillna(1.0)
    
    return rsi, atr_pct

def is_market_safe_for_buying():
    """سپر محافظ بیت‌کوین در برابر آبشارهای نزولی کل بازار"""
    btc_df = get_nobitex_candles("BTCUSDT")
    if btc_df is None or len(btc_df) < 5:
        return True
    closes = btc_df["close"]
    current_btc = float(closes.iloc[-1])
    prev_btc = float(closes.iloc[-2])
    btc_45m_ago = float(closes.iloc[-4])
    
    drop_45m = ((current_btc - btc_45m_ago) / btc_45m_ago) * 100
    if drop_45m <= -1.4 and current_btc < prev_btc:
        print("هشدار: بیت‌کوین در حال ریزش شدید است. خریدهای جدید موقتاً متوقف شدند.")
        return False
    return True

def calculate_total_equity(portfolio, latest_prices):
    """محاسبه دارایی کل (نقد + ارزش روز پوزیشن‌های باز)"""
    total = float(portfolio["balance"])
    for sym, pos in portfolio["positions"].items():
        price = latest_prices.get(sym, float(pos["buy_price"]))
        total += float(pos["amount"]) * price
    return total

def run_bot():
    portfolio = load_portfolio()
    positions = portfolio["positions"]
    cooldowns = portfolio.get("cooldowns", {})
    now_ts = int(time.time())
    
    cooldowns = {k: v for k, v in cooldowns.items() if now_ts < v}
    portfolio["cooldowns"] = cooldowns
    
    market_safe = is_market_safe_for_buying()
    top_symbols, market_info = get_top_usdt_markets(MAX_COINS_TO_SCAN)
    all_symbols = list(dict.fromkeys(list(positions.keys()) + top_symbols))
    
    print(f"شروع اسکن کامل و ایمن {len(all_symbols)} ارز در نوبیتکس...")
    buy_candidates = []
    latest_prices = {}
    
    for symbol in all_symbols:
        df = get_nobitex_candles(symbol)
        if df is None:
            time.sleep(0.12)
            continue
            
        closes = df["close"]
        current_price = float(closes.iloc[-1])
        prev_price = float(closes.iloc[-2])
        last_high = float(df["high"].iloc[-1])
        latest_prices[symbol] = current_price
        
        rsi_series, atr_pct_series = calculate_indicators(df)
        current_rsi = float(rsi_series.iloc[-1])
        prev_rsi = float(rsi_series.iloc[-2])
        prev2_rsi = float(rsi_series.iloc[-3])
        current_atr_pct = float(atr_pct_series.iloc[-1])
        
        # ==================== ۱. مدیریت هوشمند پوزیشن‌های باز (فروش) ====================
        if symbol in positions:
            pos = positions[symbol]
            buy_price = float(pos["buy_price"])
            amount = float(pos["amount"])
            invested = float(pos.get("invested", TRADE_AMOUNT))
            buy_time = int(pos.get("buy_time", now_ts))
            hours_held = (now_ts - buy_time) / 3600.0
            
            peak_candidate = max(current_price, last_high) if (now_ts - buy_time >= 900) else current_price
            highest_price = max(float(pos.get("highest_price", buy_price)), peak_candidate)
            pos["highest_price"] = highest_price
            
            gross_profit_pct = ((current_price - buy_price) / buy_price) * 100
            max_profit_pct = ((highest_price - buy_price) / buy_price) * 100
            drop_from_peak_pct = ((highest_price - current_price) / highest_price) * 100
            
            # کسر دقیق کارمزد رفت و برگشت صرافی
            gross_value = amount * current_price
            fee_cost = (invested * (FEE_PERCENT / 200.0)) + (gross_value * (FEE_PERCENT / 200.0))
            net_sell_value = gross_value - fee_cost
            net_profit_usdt = net_sell_value - invested
            net_profit_pct = (net_profit_usdt / invested) * 100
            
            if max_profit_pct >= BREAKEVEN_TRIGGER:
                pos["breakeven_active"] = True
                
            dynamic_trailing_step = 1.2 if max_profit_pct >= 5.0 else 0.75
            
            should_sell = False
            sell_reason = ""
            is_stop_loss = False
            
            # الف) قفل سود متحرک داینامیک
            if max_profit_pct >= TRAILING_START and (drop_from_peak_pct >= dynamic_trailing_step or current_rsi >= 75):
                should_sell = True
                sell_reason = f"قفل سود متحرک (سقف لمس‌شده: {max_profit_pct:.2f}%)"
                
            # ب) خروج در اوج اشباع خرید
            elif current_rsi >= 78 and net_profit_pct >= 1.5:
                should_sell = True
                sell_reason = f"خروج در اوج اشباع خرید (RSI: {current_rsi:.1f})"
                
            # ج) سپر محافظ ریسک‌فری
            elif pos.get("breakeven_active", False) and gross_profit_pct <= BREAKEVEN_FLOOR:
                should_sell = True
                sell_reason = f"سپر محافظ سود (برگشت از سقف {max_profit_pct:.2f}%)"
                
            # د) آزادسازی سرمایه از ارز راکد
            elif hours_held >= STAGNANT_HOURS and net_profit_pct >= 0.15 and current_rsi >= 50:
                should_sell = True
                sell_reason = f"آزادسازی سرمایه راکد پس از {hours_held:.0f} ساعت (با سود)"
                
            # هـ) حد ضرر قطعی
            elif gross_profit_pct <= STOP_LOSS_LIMIT:
                should_sell = True
                is_stop_loss = True
                sell_reason = "حد ضرر هوشمند (جلوگیری از ریزش بیشتر)"
                
            if should_sell:
                portfolio["balance"] += net_sell_value
                portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
                if net_profit_usdt > 0:
                    portfolio["win_trades"] = portfolio.get("win_trades", 0) + 1
                if is_stop_loss:
                    portfolio["cooldowns"][symbol] = now_ts + COOLDOWN_SECONDS
                    
                del portfolio["positions"][symbol]
                
                total_t = portfolio["total_trades"]
                win_rate = (portfolio.get("win_trades", 0) / total_t) * 100 if total_t > 0 else 0
                total_equity = calculate_total_equity(portfolio, latest_prices)
                
                msg = (
                    f"⭕️ گزارش فروش خودکار ({symbol})\n\n"
                    f"✅ علت خروج: {sell_reason}\n"
                    f"✅ قیمت خرید: {format_price(buy_price)} | فروش: {format_price(current_price)}\n"
                    f"✅ سود/زیان خالص (کسر کارمزد): {net_profit_pct:+.2f}% ({net_profit_usdt:+.2f} تتر)\n"
                    f"✅ آمار کلی: {total_t} معامله (نرخ برد: {win_rate:.0f}%)\n"
                    f"✅ موجودی نقد: {portfolio['balance']:.2f} تتر\n"
                    f"🛑 ارزش کل دارایی (نقد + پوزیشن‌ها): {total_equity:.2f} تتر"
                )
                send_telegram(msg)
                
        # ==================== ۲. شکار فرصت‌های خرید بهینه ====================
        elif market_safe and len(positions) < MAX_OPEN_POSITIONS:
            if symbol in cooldowns:
                continue
                
            spread = market_info.get(symbol, {}).get("spread", 0.2)
            if spread > MAX_SPREAD_PERCENT:
                continue
                
            was_oversold = (current_rsi <= RSI_BUY_LIMIT or prev_rsi <= RSI_BUY_LIMIT or prev2_rsi <= RSI_BUY_LIMIT)
            is_rebounding = (current_price > prev_price and current_rsi > prev_rsi and 25.0 <= current_rsi <= 42.0)
            has_good_volatility = (current_atr_pct >= 0.45)
            
            if was_oversold and is_rebounding and has_good_volatility:
                score = (45.0 - current_rsi) + (current_atr_pct * 2.0)
                buy_candidates.append({
                    "symbol": symbol,
                    "price": current_price,
                    "rsi": current_rsi,
                    "score": score
                })
                
        time.sleep(0.12)

    # ==================== ۳. ثبت خریدهای پرامتیاز ====================
    buy_candidates.sort(key=lambda x: x["score"], reverse=True)
    buys_this_run = 0
    
    for candidate in buy_candidates:
        if (portfolio["balance"] >= TRADE_AMOUNT and 
            buys_this_run < MAX_BUYS_PER_RUN and 
            len(portfolio["positions"]) < MAX_OPEN_POSITIONS):
            
            sym = candidate["symbol"]
            price = candidate["price"]
            rsi_val = candidate["rsi"]
            
            amount = TRADE_AMOUNT / price
            portfolio["balance"] -= TRADE_AMOUNT
            portfolio["positions"][sym] = {
                "buy_price": price,
                "amount": amount,
                "invested": TRADE_AMOUNT,
                "highest_price": price,
                "breakeven_active": False,
                "buy_time": now_ts
            }
            buys_this_run += 1
            total_equity = calculate_total_equity(portfolio, latest_prices)
            
            msg = (
                f"⭕️ شکار فرصت خرید جدید ({sym})\n\n"
                f"✅ قیمت ورود: {format_price(price)} تتر\n"
                f"✅ شاخص RSI: {rsi_val:.1f} (تأیید چرخش از کف + نقدینگی فعال)\n"
                f"✅ مبلغ سرمایه‌گذاری: {TRADE_AMOUNT} تتر\n"
                f"✅ موجودی نقد باقی‌مانده: {portfolio['balance']:.2f} تتر\n"
                f"🛑 ارزش کل دارایی (نقد + پوزیشن‌ها): {total_equity:.2f} تتر"
            )
            send_telegram(msg)
        else:
            break

    save_portfolio(portfolio)

if __name__ == "__main__":
    run_bot()
