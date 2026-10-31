import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ==================== تنظیمات موتور دائمی ۵۰ دقیقه‌ای و اسکن ۱۶۰+ ارز ====================
RUN_DURATION_SECONDS = 50 * 60  # روشن ماندن دائمی به مدت ۵۰ دقیقه در هر استارت
SCAN_INTERVAL_SECONDS = 180     # فاصله بین هر دور اسکن کامل بازار (۳ دقیقه)
MAX_COINS_TO_SCAN = 180         # بررسی تا ۱۸۰ بازار فعال (بیش از ۱۶۰ ارز)

TRADE_AMOUNT = 20.0             # مبلغ ورود در هر پله خرید (معادل ۲۰ تتر)
MAX_BUYS_PER_CYCLE = 2          # حداکثر خرید در هر دور اسکن
MAX_OPEN_POSITIONS = 5          # حداکثر تعداد معاملات باز همزمان
FEE_PERCENT = 0.35              # کارمزد رفت و برگشت صرافی (درصد)
MAX_SPREAD_PERCENT = 0.65       # فیلتر سخت‌گیرانه شکاف قیمت تابلو برای جلوگیری از ضرر ورود

# ==================== تنظیمات حد ضرر نزدیک به صفر و حداکثر سود ====================
RSI_BUY_LIMIT = 30.0            # ورود فقط در کف‌های عمیق و مطمئن
ZERO_LOSS_TRIGGER = 0.65        # به محض ۰.۶۵٪ رشد، حد ضرر به نقطه صفر (بدون ضرر) منتقل می‌شود!
ZERO_LOSS_FLOOR = 0.40          # کف خروج تضمینی با سود خالص پس از فعال شدن قفل ضدضرر
TRAILING_START = 1.50           # شروع تعقیب سودهای بزرگ از ۱.۵٪ به بالا
INITIAL_STOP_LOSS = -0.65       # حد ضرر اولیه بسیار نزدیک به صفر برای قطع سریع ریسک
COOLDOWN_SECONDS = 2 * 3600     # ۲ ساعت استراحت برای ارزی که بسته شده است
STAGNANT_HOURS = 4              # آزادسازی سریع سرمایه اگر ارز بیش از ۴ ساعت درجا بزند

NOBITEX_HOSTS = [
    "https://apiv2.nobitex.ir",
    "https://api.nobitex.ir"
]

# لیست سیاه کامل تمام استیبل‌کوین‌ها و ارزهای با قیمت ثابت (حذف ۱۰۰ درصدی از معیار)
STABLECOINS_BLACKLIST = {
    "USDT", "DAI", "USDC", "BUSD", "TUSD", "USDP", "FDUSD", 
    "PYUSD", "PAXG", "XAUT", "USDE", "EURT", "AEUR", "UST", "CUSD"
}

PORTFOLIO_FILE = "portfolio.json"

def create_resilient_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    })
    return session

SESSION = create_resilient_session()

def request_nobitex(endpoint, timeout=10):
    for base_url in NOBITEX_HOSTS:
        try:
            res = SESSION.get(f"{base_url}{endpoint}", timeout=timeout)
            if res.status_code == 200:
                return res.json()
        except Exception:
            continue
    return None

def format_price(price):
    if price >= 1000:
        return f"{price:,.0f}"
    elif price >= 10:
        return f"{price:,.2f}"
    elif price >= 0.1:
        return f"{price:.4f}"
    else:
        return f"{price:.8f}".rstrip("0").rstrip(".")

def get_tehran_time():
    try:
        return datetime.now(ZoneInfo("Asia/Tehran")).strftime("%H:%M:%S")
    except Exception:
        return datetime.now().strftime("%H:%M:%S")

def is_silent_hours():
    """ارسال بی‌صدای پیام‌ها بین ساعت ۲۲:۴۵ تا ۰۷:۴۵ به وقت تهران"""
    try:
        now = datetime.now(ZoneInfo("Asia/Tehran"))
        mins = now.hour * 60 + now.minute
        return mins >= 1365 or mins <= 465
    except Exception:
        return False

def send_telegram(text):
    """ارسال پیام جدید در تلگرام و بازگرداندن شناسه پیام (message_id) برای آپدیت پویا"""
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_notification": is_silent_hours()
    }
    try:
        r = SESSION.post(url, json=payload, timeout=10).json()
        if r.get("ok"):
            return r["result"]["message_id"]
    except Exception as e:
        print(f"خطا در ارسال تلگرام: {e}")
    return None

def edit_telegram(message_id, text):
    """ویرایش زنده و پویا پیام داشبورد در تلگرام بدون ایجاد اسپم"""
    if not BOT_TOKEN or not CHAT_ID or not message_id:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text
    }
    try:
        r = SESSION.post(url, json=payload, timeout=10).json()
        return r.get("ok", False)
    except Exception:
        return False

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "balance" in data and "positions" in data:
                    data.setdefault("cooldowns", {})
                    data.setdefault("total_trades", 0)
                    data.setdefault("win_trades", 0)
                    data.setdefault("total_net_profit", 0.0)
                    return data
        except Exception:
            pass
    
    return {
        "balance": 100.0,
        "total_trades": 0,
        "win_trades": 0,
        "total_net_profit": 0.0,
        "positions": {},
        "cooldowns": {}
    }

def save_portfolio(data):
    temp_file = f"{PORTFOLIO_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, PORTFOLIO_FILE)
    except Exception as e:
        print(f"خطا در ذخیره حافظه: {e}")

def get_all_active_markets(limit=180):
    """دریافت بیش از ۱۶۰ بازار فعال نوبیتکس (تتری + تومانی) با حذف کامل استیبل‌کوین‌ها"""
    res = request_nobitex("/market/stats", timeout=15)
    market_info = {}
    if res and res.get("status") == "ok":
        stats = res.get("stats", {})
        candidates = []
        
        for pair, data in stats.items():
            if data.get("isClosed", False):
                continue
            parts = pair.split("-")
            if len(parts) != 2:
                continue
            base_coin, quote_coin = parts[0].upper(), parts[1].upper()
            
            # ۱. حذف ۱۰۰ درصدی استیبل‌کوین‌ها از ارزهای مورد بررسی
            if base_coin in STABLECOINS_BLACKLIST:
                continue
                
            if quote_coin not in ["USDT", "IRT"]:
                continue
                
            symbol = f"{base_coin}{quote_coin}"
            volume_dst = float(data.get("volumeDst", 0))
            best_buy = float(data.get("bestBuy", 0))
            best_sell = float(data.get("bestSell", 0))
            latest = float(data.get("latest", best_sell))
            
            if best_buy <= 0 or best_sell <= 0:
                continue
                
            spread_pct = ((best_sell - best_buy) / best_buy) * 100
            
            # نرمال‌سازی حجم معاملات برای رتبه‌بندی منصفانه بین بازارهای تتری و تومانی
            norm_vol = volume_dst if quote_coin == "USDT" else (volume_dst / 60000.0)
            
            market_info[symbol] = {
                "base": base_coin,
                "quote": quote_coin,
                "spread": spread_pct,
                "volume": norm_vol,
                "latest": latest
            }
            
            if norm_vol >= 500:
                candidates.append((symbol, norm_vol))
                
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_list = [item[0] for item in candidates[:limit]]
        if len(top_list) >= 20:
            return top_list, market_info
            
    fallback = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TONUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "SHIBUSDT", "TRXUSDT"]
    return fallback, market_info

def get_nobitex_candles(symbol):
    to_time = int(time.time())
    from_time = to_time - (90 * 15 * 60)
    endpoint = f"/market/udf/history?symbol={symbol}&resolution=15&from={from_time}&to={to_time}"
    res = request_nobitex(endpoint, timeout=8)
    if res and res.get("s") == "ok" and len(res.get("c", [])) >= 25:
        return pd.DataFrame({
            "close": pd.Series(res["c"], dtype=float),
            "high": pd.Series(res["h"], dtype=float),
            "low": pd.Series(res["l"], dtype=float)
        })
    return None

def calculate_indicators(df, period=14):
    closes = df["close"]
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean().replace(0, 1e-10)
    
    rs = avg_gain / avg_loss
    rsi = (100 - (100 / (1 + rs))).fillna(50.0)
    
    high_low = df["high"] - df["low"]
    atr_pct = (high_low / closes * 100).rolling(window=14).mean().fillna(1.0)
    
    return rsi, atr_pct

def check_market_safety():
    btc_df = get_nobitex_candles("BTCUSDT")
    if btc_df is None or len(btc_df) < 5:
        return True, 0.0, 0.0
    closes = btc_df["close"]
    cur_btc = float(closes.iloc[-1])
    prev_btc = float(closes.iloc[-2])
    btc_45m = float(closes.iloc[-4])
    
    change_45m = ((cur_btc - btc_45m) / btc_45m) * 100
    if change_45m <= -1.2 and cur_btc < prev_btc:
        return False, cur_btc, change_45m
    return True, cur_btc, change_45m

def calculate_total_equity(portfolio, latest_prices):
    total = float(portfolio["balance"])
    for sym, pos in portfolio["positions"].items():
        cur_p = latest_prices.get(sym, float(pos["buy_price"]))
        buy_p = float(pos["buy_price"])
        invested = float(pos.get("invested", TRADE_AMOUNT))
        # محاسبه ارزش دلاری پوزیشن (حتی اگر جفت‌ارز تومانی باشد، بر اساس درصد تغییر نسبت به ۲۰ تتر محاسبه می‌شود)
        gross_ratio = cur_p / buy_p if buy_p > 0 else 1.0
        total += invested * gross_ratio
    return total

def execute_single_scan_cycle(portfolio, cycle_num, start_ts, dashboard_msg_id):
    positions = portfolio["positions"]
    cooldowns = portfolio.get("cooldowns", {})
    now_ts = int(time.time())
    
    cooldowns = {k: v for k, v in cooldowns.items() if now_ts < v}
    portfolio["cooldowns"] = cooldowns
    
    market_safe, btc_price, btc_change = check_market_safety()
    top_symbols, market_info = get_all_active_markets(MAX_COINS_TO_SCAN)
    all_symbols = list(dict.fromkeys(list(positions.keys()) + top_symbols))
    
    buy_candidates = []
    latest_prices = {}
    scanned_summary = []
    
    # ثبت قیمت‌های لحظه‌ای اولیه از استتس برای سرعت بالا
    for sym, info in market_info.items():
        latest_prices[sym] = info["latest"]

    for symbol in all_symbols:
        df = get_nobitex_candles(symbol)
        if df is None:
            time.sleep(0.06)
            continue
            
        closes = df["close"]
        current_price = float(closes.iloc[-1])
        prev_price = float(closes.iloc[-2])
        last_high = float(df["high"].iloc[-1])
        last_low = float(df["low"].iloc[-1])
        latest_prices[symbol] = current_price
        
        rsi_series, atr_pct_series = calculate_indicators(df)
        current_rsi = float(rsi_series.iloc[-1])
        prev_rsi = float(rsi_series.iloc[-2])
        prev2_rsi = float(rsi_series.iloc[-3])
        current_atr_pct = float(atr_pct_series.iloc[-1])
        
        scanned_summary.append({
            "symbol": symbol,
            "price": current_price,
            "rsi": current_rsi
        })
        
        # ==================== ۱. مدیریت پوزیشن‌های باز (حد ضرر نزدیک به صفر + حداکثر سود) ====================
        if symbol in positions:
            pos = positions[symbol]
            buy_price = float(pos["buy_price"])
            invested = float(pos.get("invested", TRADE_AMOUNT))
            buy_time = int(pos.get("buy_time", now_ts))
            hours_held = (now_ts - buy_time) / 3600.0
            
            peak_candidate = max(current_price, last_high) if (now_ts - buy_time >= 600) else current_price
            highest_price = max(float(pos.get("highest_price", buy_price)), peak_candidate)
            pos["highest_price"] = highest_price
            
            gross_profit_pct = ((current_price - buy_price) / buy_price) * 100
            max_profit_pct = ((highest_price - buy_price) / buy_price) * 100
            drop_from_peak_pct = ((highest_price - current_price) / highest_price) * 100
            
            net_profit_pct = gross_profit_pct - FEE_PERCENT
            net_profit_usdt = invested * (net_profit_pct / 100.0)
            net_sell_value = invested + net_profit_usdt
            
            # فعال‌سازی فوری قفل ضدضرر (Zero-Loss Shield) به محض ۰.۶۵٪ رشد!
            if max_profit_pct >= ZERO_LOSS_TRIGGER:
                pos["zero_loss_active"] = True
                
            # تنظیم هوشمند فاصله تعقیب سود برای شکار پامپ‌های بزرگ
            if max_profit_pct >= 6.0:
                dynamic_step = 1.1
            elif max_profit_pct >= 3.0:
                dynamic_step = 0.75
            else:
                dynamic_step = 0.50
                
            should_sell = False
            sell_reason = ""
            is_loss = False
            
            # الف) شکار حداکثر سود با حد سود متحرک داینامیک
            if max_profit_pct >= TRAILING_START and (drop_from_peak_pct >= dynamic_step or current_rsi >= 76):
                should_sell = True
                sell_reason = f"قفل حداکثر سود متحرک (سقف لمس‌شده: {max_profit_pct:.2f}%)"
                
            # ب) خروج در اوج اشباع خرید
            elif current_rsi >= 79 and net_profit_pct >= 1.2:
                should_sell = True
                sell_reason = f"فروش در قله اشباع خرید (RSI: {current_rsi:.1f})"
                
            # ج) سپر حد ضرر صفر (خروج تضمینی با سود در صورت برگشت بازار)
            elif pos.get("zero_loss_active", False) and gross_profit_pct <= ZERO_LOSS_FLOOR:
                should_sell = True
                sell_reason = f"سپر ریسک‌صفر (محافظت کامل از اصل سرمایه در برگشت از {max_profit_pct:.2f}%)"
                
            # د) آزادسازی سریع سرمایه راکد
            elif hours_held >= STAGNANT_HOURS and net_profit_pct >= 0.10:
                should_sell = True
                sell_reason = f"آزادسازی سرمایه از ارز راکد ({hours_held:.1f} ساعت)"
                
            # هـ) حد ضرر فوق‌سریع نزدیک به صفر (جلوگیری قاطع از ریزش)
            elif gross_profit_pct <= INITIAL_STOP_LOSS:
                should_sell = True
                is_loss = True
                sell_reason = "توقف اضطراری نزدیک به صفر (جلوگیری از ضرر)"
                
            if should_sell:
                portfolio["balance"] += net_sell_value
                portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
                portfolio["total_net_profit"] = portfolio.get("total_net_profit", 0.0) + net_profit_usdt
                if net_profit_usdt >= 0:
                    portfolio["win_trades"] = portfolio.get("win_trades", 0) + 1
                if is_loss:
                    portfolio["cooldowns"][symbol] = now_ts + COOLDOWN_SECONDS
                    
                del portfolio["positions"][symbol]
                
                total_t = portfolio["total_trades"]
                win_rate = (portfolio.get("win_trades", 0) / total_t) * 100 if total_t > 0 else 0
                total_eq = calculate_total_equity(portfolio, latest_prices)
                
                msg = (
                    f"⭕️ بسته شدن معامله ({symbol}) | ساعت {get_tehran_time()}\n\n"
                    f"✅ مکانیزم خروج: {sell_reason}\n"
                    f"✅ قیمت ورود: {format_price(buy_price)} | خروج: {format_price(current_price)}\n"
                    f"✅ نتیجه خالص: {net_profit_pct:+.2f}% ({net_profit_usdt:+.2f} تتر)\n"
                    f"✅ کارنامه: {total_t} معامله (برد/سر‌به‌سر: {win_rate:.0f}%)\n"
                    f"🛑 ارزش کل دارایی: {total_eq:.2f} تتر"
                )
                send_telegram(msg)
                
        # ==================== ۲. شکار اسنایپری در کف مطلق (برای جلوگیری از ضرر اولیه) ====================
        elif market_safe and len(positions) < MAX_OPEN_POSITIONS:
            base_coin = market_info.get(symbol, {}).get("base", symbol.replace("USDT", "").replace("IRT", ""))
            if base_coin in STABLECOINS_BLACKLIST or symbol in cooldowns:
                continue
                
            # جلوگیری از خرید همزمان یک ارز در دو بازار تتری و تومانی
            already_holding_base = any(
                market_info.get(open_sym, {}).get("base") == base_coin for open_sym in positions.keys()
            )
            if already_holding_base:
                continue
                
            spread = market_info.get(symbol, {}).get("spread", 1.0)
            if spread > MAX_SPREAD_PERCENT:
                continue
                
            # شروط سخت‌گیرانه اسنایپری: کف عمیق RSI + تایید برگشت قیمت بالاتر از کف کندل + نوسان مفید
            was_deep_oversold = (current_rsi <= RSI_BUY_LIMIT or prev_rsi <= RSI_BUY_LIMIT or prev2_rsi <= RSI_BUY_LIMIT)
            strong_rebound = (current_price > prev_price and current_rsi > prev_rsi + 0.8 and current_price > last_low)
            good_volatility = (current_atr_pct >= 0.45)
            
            if was_deep_oversold and strong_rebound and good_volatility:
                score = (50.0 - current_rsi) + (current_atr_pct * 3.0) - (spread * 5.0)
                buy_candidates.append({
                    "symbol": symbol,
                    "price": current_price,
                    "rsi": current_rsi,
                    "spread": spread,
                    "score": score
                })
                
        time.sleep(0.06)

    # ==================== ۳. ثبت خرید برای بهترین شکارها ====================
    buy_candidates.sort(key=lambda x: x["score"], reverse=True)
    buys_done = 0
    
    for cand in buy_candidates:
        if (portfolio["balance"] >= TRADE_AMOUNT and 
            buys_done < MAX_BUYS_PER_CYCLE and 
            len(portfolio["positions"]) < MAX_OPEN_POSITIONS):
            
            sym = cand["symbol"]
            price = cand["price"]
            rsi_val = cand["rsi"]
            
            portfolio["balance"] -= TRADE_AMOUNT
            portfolio["positions"][sym] = {
                "buy_price": price,
                "invested": TRADE_AMOUNT,
                "highest_price": price,
                "zero_loss_active": False,
                "buy_time": now_ts
            }
            buys_done += 1
            total_eq = calculate_total_equity(portfolio, latest_prices)
            
            msg = (
                f"⭕️ شکار اسنایپری جدید ({sym}) | ساعت {get_tehran_time()}\n\n"
                f"✅ قیمت ورود: {format_price(price)}\n"
                f"✅ شاخص RSI: {rsi_val:.1f} (تأیید چرخش قوی از کف)\n"
                f"✅ سپر محافظ: انتقال خودکار حد ضرر به نقطه صفر در +0.65%\n"
                f"✅ مبلغ ورود: {TRADE_AMOUNT} تتر | نقد باقی‌مانده: {portfolio['balance']:.2f} تتر\n"
                f"🛑 ارزش کل دارایی: {total_eq:.2f} تتر"
            )
            send_telegram(msg)
        else:
            break

    save_portfolio(portfolio)

    # ==================== ۴. آپدیت داشبورد زنده و پویا در تلگرام ====================
    if scanned_summary:
        scanned_summary.sort(key=lambda x: x["rsi"])
        watch_list = " | ".join([f"{c['symbol']}: {c['rsi']:.1f}" for c in scanned_summary[:4]])
        
        pos_lines = []
        for sym, pos in portfolio["positions"].items():
            cur_p = latest_prices.get(sym, float(pos["buy_price"]))
            buy_p = float(pos["buy_price"])
            pnl = ((cur_p - buy_p) / buy_p) * 100 - FEE_PERCENT
            shield = "🛡️ریسك‌فری" if pos.get("zero_loss_active") else "🎯در کمین"
            pos_lines.append(f"   🔹 {sym}: {pnl:+.2f}% ({shield})")
            
        pos_block = "\n".join(pos_lines) if pos_lines else "   🔹 معامله بازی نیست (در حال رصد برای شکار بدون ریسک)"
        safety_str = f"🟢 امن (BTC: {format_price(btc_price)}$)" if market_safe else f"🔴 هشدار ریزش BTC ({btc_change:+.1f}%)"
        
        elapsed_min = int((time.time() - start_ts) / 60)
        rem_min = max(0, 50 - elapsed_min)
        total_eq = calculate_total_equity(portfolio, latest_prices)
        total_pnl = total_eq - 100.0
        
        live_text = (
            f"⭕️ داشبورد زنده ربات اسنایپر نوبیتکس (پویا)\n"
            f"⏱ آخرین بروزرسانی: ساعت {get_tehran_time()} | دور اسکن: #{cycle_num}\n"
            f"🔄 موتور دائمی ۵۰ دقیقه‌ای: فعال ({rem_min} دقیقه تا تمدید خودکار)\n\n"
            f"✅ بازارهای اسکن‌شده (بدون استیبل‌کوین): {len(scanned_summary)} ارز\n"
            f"✅ وضعیت ایمنی بازار: {safety_str}\n"
            f"✅ ارزهای در کمین شکار (کمترین RSI):\n"
            f"   🔸 {watch_list}\n\n"
            f"✅ وضعیت لحظه‌ای سبد معاملات:\n{pos_block}\n\n"
            f"✅ موجودی نقد: {portfolio['balance']:.2f} تتر | کل معاملات: {portfolio.get('total_trades',0)}\n"
            f"🛑 ارزش کل دارایی: {total_eq:.2f} تتر (برآیند کل: {total_pnl:+.2f} تتر)"
        )
        
        if dashboard_msg_id:
            ok = edit_telegram(dashboard_msg_id, live_text)
            if not ok:
                dashboard_msg_id = send_telegram(live_text)
        else:
            dashboard_msg_id = send_telegram(live_text)
            
    return dashboard_msg_id

def run_continuous_50m_engine():
    start_ts = time.time()
    portfolio = load_portfolio()
    
    # ارسال پیام استارت پنل زنده در ابتدای بازه ۵۰ دقیقه‌ای
    init_text = (
        f"⭕️ موتور دائمی ۵۰ دقیقه‌ای روشن شد! (ساعت {get_tehran_time()})\n\n"
        f"✅ در حال اسکن بیش از ۱۶۰ ارز نوبیتکس (استیبل‌کوین‌ها حذف شدند)...\n"
        f"✅ این پیام به صورت زنده و پویا هر ۳ دقیقه آپدیت می‌شود."
    )
    dashboard_msg_id = send_telegram(init_text)
    
    cycle = 1
    while (time.time() - start_ts) < RUN_DURATION_SECONDS:
        cycle_start = time.time()
        print(f"--- شروع دور #{cycle} اسکن ۱۶۰+ ارز ---")
        try:
            dashboard_msg_id = execute_single_scan_cycle(portfolio, cycle, start_ts, dashboard_msg_id)
        except Exception as e:
            print(f"خطا در دور {cycle}: {e}")
            
        cycle += 1
        elapsed = time.time() - start_ts
        if elapsed + SCAN_INTERVAL_SECONDS >= RUN_DURATION_SECONDS:
            break
            
        sleep_time = max(15, SCAN_INTERVAL_SECONDS - int(time.time() - cycle_start))
        time.sleep(sleep_time)
        
    print("پایان چرخه ۵۰ دقیقه‌ای. ذخیره نهایی کیف پول و تحویل به نوبت بعدی...")
    save_portfolio(portfolio)

if __name__ == "__main__":
    run_continuous_50m_engine()
