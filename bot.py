import os
import json
import time
import math
import random
import requests
import numpy as np
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# خواندن اطلاعات امنیتی از بخش Secrets گیت‌هاب (کاملاً امن برای مخزن Public)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ==================== تنظیمات موتور دائمی ۵۰ دقیقه‌ای و اسکن ۱۶۰+ ارز ====================
RUN_DURATION_SECONDS = 50 * 60      # روشن ماندن مداوم به مدت ۵۰ دقیقه در هر نوبت
SCAN_INTERVAL_SECONDS = 180         # فاصله بین هر دور اسکن کامل بازار (۳ دقیقه)
MAX_COINS_TO_SCAN = 180             # پایش تا ۱۸۰ بازار فعال (بیش از ۱۶۰ ارز بدون استیبل‌کوین)

# ==================== پارامترهای مدیریت سرمایه و ریسک (بند ۴ و ۶ دستورالعمل) ====================
INITIAL_CAPITAL = 100.0             # سرمایه اولیه: ۱۰۰ تتر
MIN_RISK_PER_TRADE_PCT = 0.50       # حداقل ریسک مجاز در هر معامله (۰.۵٪ سرمایه)
MAX_RISK_PER_TRADE_PCT = 1.00       # حداکثر ریسک مجاز در هر معامله (۱.۰٪ سرمایه)
MAX_TOTAL_OPEN_RISK_PCT = 2.00      # سقف ریسک تجمعی تمام معاملات باز همزمان (۲.۰٪ سرمایه)
MAX_DAILY_LOSS_PCT = 2.00           # حداکثر ضرر روزانه مجاز: ۲٪ سرمایه ابتدای روز
MAX_WEEKLY_LOSS_PCT = 5.00          # حداکثر ضرر هفتگی مجاز: ۵٪ سرمایه ابتدای هفته
MAX_DRAWDOWN_HALT_PCT = 10.00       # توقف کامل معاملات در صورت افت ۱۰٪ از سقف سرمایه

MIN_POS_SIZE_USDT = 10.0            # کف حجم هر معامله برای رعایت حداقل سفارش صرافی
MAX_POS_SIZE_USDT = 25.0            # سقف حجم هر معامله (حداکثر ۲۵٪ سبد در یک ارز)
MAX_OPEN_POSITIONS = 4              # حداکثر تعداد معاملات باز همزمان
MAX_BUYS_PER_CYCLE = 2              # حداکثر خرید جدید در هر دور اسکن
MAX_CORRELATION_LIMIT = 0.78        # سقف هم‌بستگی مجاز بین ارز جدید و ارزهای موجود در سبد

# ==================== پارامترهای موتور تصمیم‌گیری و مدیریت معامله (بند ۳ و ۵) ====================
FEE_ROUNDTRIP_PCT = 0.35            # مجموع کارمزد خرید و فروش در صرافی (درصد)
MAX_SPREAD_PCT = 0.60               # حداکثر اسپرد مجاز تابلوی سفارشات (درصد)
MIN_SIGNAL_SCORE = 74.0             # حداقل امتیاز کیفیت سیگنال از ۱۰۰ برای مجوز ورود
MIN_RR_RATIO = 2.0                  # حداقل نسبت ریسک به بازده خالص (۱:۲)
MIN_EXPECTANCY_R = 0.20             # حداقل امید ریاضی خالص مورد انتظار بر حسب واحد ریسک (R)

BREAKEVEN_TRIGGER_R = 0.75          # انتقال حد ضرر به نقطه سربه‌سر (ریسک‌صفر) در ۰.۷۵ برابر ریسک
TRAILING_ACTIVATE_R = 1.30          # فعال‌سازی حد سود متحرک پویا پس از ۱.۳ برابر ریسک
ATR_SL_MULTIPLIER = 1.50            # ضریب ATR برای تعیین حد ضرر ساختاری
ATR_TRAIL_MULTIPLIER = 1.40         # ضریب ATR برای تعقیب پویا و شکار حداکثر سود
COOLDOWN_SECONDS = 3 * 3600         # ۳ ساعت ممنوعیت ورود مجدد پس از فعال شدن حد ضرر
STAGNANT_HOURS = 6.0                # آزادسازی سرمایه در صورت عدم حرکت پس از ۶ ساعت

NOBITEX_HOSTS = [
    "https://apiv2.nobitex.ir",
    "https://api.nobitex.ir"
]

# لیست سیاه کامل استیبل‌کوین‌ها و دارایی‌های با قیمت ثابت (حذف ۱۰۰٪ از معیار)
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


def get_tehran_dt():
    try:
        return datetime.now(ZoneInfo("Asia/Tehran"))
    except Exception:
        return datetime.utcnow()


def get_tehran_time_str():
    return get_tehran_dt().strftime("%H:%M:%S")


def is_silent_hours():
    """ارسال بی‌صدای پیام‌ها بین ساعت ۲۲:۴۵ شب تا ۰۷:۴۵ صبح به وقت تهران"""
    now = get_tehran_dt()
    mins = now.hour * 60 + now.minute
    return mins >= 1365 or mins <= 465


def send_telegram(text):
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


# ==================== مدیریت حافظه، دوره مالی و کارنامه کوانت ====================
def load_portfolio():
    now_dt = get_tehran_dt()
    day_key = now_dt.strftime("%Y-%m-%d")
    week_key = now_dt.strftime("%Y-W%W")

    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "balance" in data and "positions" in data:
                    data.setdefault("peak_equity", INITIAL_CAPITAL)
                    data.setdefault("day_key", day_key)
                    data.setdefault("day_start_equity", INITIAL_CAPITAL)
                    data.setdefault("week_key", week_key)
                    data.setdefault("week_start_equity", INITIAL_CAPITAL)
                    data.setdefault("total_trades", 0)
                    data.setdefault("win_trades", 0)
                    data.setdefault("gross_profit", 0.0)
                    data.setdefault("gross_loss", 0.0)
                    data.setdefault("total_fees_paid", 0.0)
                    data.setdefault("max_drawdown_pct", 0.0)
                    data.setdefault("trade_returns_pct", [])
                    data.setdefault("trade_r_multiples", [])
                    data.setdefault("cooldowns", {})
                    data.setdefault("system_halted", False)
                    data.setdefault("halt_reason", "")
                    return data
        except Exception as e:
            print(f"خطا در خواندن فایل حافظه: {e}")

    return {
        "balance": INITIAL_CAPITAL,
        "peak_equity": INITIAL_CAPITAL,
        "day_key": day_key,
        "day_start_equity": INITIAL_CAPITAL,
        "week_key": week_key,
        "week_start_equity": INITIAL_CAPITAL,
        "total_trades": 0,
        "win_trades": 0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "total_fees_paid": 0.0,
        "max_drawdown_pct": 0.0,
        "trade_returns_pct": [],
        "trade_r_multiples": [],
        "positions": {},
        "cooldowns": {},
        "system_halted": False,
        "halt_reason": ""
    }


def save_portfolio(data):
    temp_file = f"{PORTFOLIO_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, PORTFOLIO_FILE)
    except Exception as e:
        print(f"خطا در ذخیره حافظه: {e}")


def calculate_total_equity(portfolio, latest_prices):
    total = float(portfolio["balance"])
    for sym, pos in portfolio["positions"].items():
        cur_p = latest_prices.get(sym, float(pos["buy_price"]))
        buy_p = float(pos["buy_price"])
        invested = float(pos["invested"])
        ratio = (cur_p / buy_p) if buy_p > 0 else 1.0
        total += invested * ratio
    return total


def update_risk_watermarks_and_check_breakers(portfolio, current_equity):
    """بررسی فیوزهای کنترل بحران: ضرر روزانه (۲٪)، هفتگی (۵٪) و افت کل (۱۰٪)"""
    now_dt = get_tehran_dt()
    day_key = now_dt.strftime("%Y-%m-%d")
    week_key = now_dt.strftime("%Y-W%W")

    if portfolio.get("day_key") != day_key:
        portfolio["day_key"] = day_key
        portfolio["day_start_equity"] = current_equity

    if portfolio.get("week_key") != week_key:
        portfolio["week_key"] = week_key
        portfolio["week_start_equity"] = current_equity

    if current_equity > portfolio.get("peak_equity", INITIAL_CAPITAL):
        portfolio["peak_equity"] = current_equity

    peak = max(float(portfolio["peak_equity"]), 1.0)
    day_start = max(float(portfolio["day_start_equity"]), 1.0)
    week_start = max(float(portfolio["week_start_equity"]), 1.0)

    current_dd_pct = max(0.0, ((peak - current_equity) / peak) * 100.0)
    if current_dd_pct > portfolio.get("max_drawdown_pct", 0.0):
        portfolio["max_drawdown_pct"] = current_dd_pct

    daily_loss_pct = ((day_start - current_equity) / day_start) * 100.0
    weekly_loss_pct = ((week_start - current_equity) / week_start) * 100.0

    if current_dd_pct >= MAX_DRAWDOWN_HALT_PCT:
        portfolio["system_halted"] = True
        portfolio["halt_reason"] = f"توقف اضطراری: افت سرمایه به {current_dd_pct:.2f}% رسید (سقف مجاز ۱۰٪)"
        return False, portfolio["halt_reason"]

    if portfolio.get("system_halted"):
        return False, portfolio.get("halt_reason", "سیستم متوقف است")

    if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
        return False, f"قفل ریسک روزانه فعال شد (ضرر امروز: {daily_loss_pct:.2f}% از سقف ۲٪)"

    if weekly_loss_pct >= MAX_WEEKLY_LOSS_PCT:
        return False, f"قفل ریسک هفتگی فعال شد (ضرر هفته: {weekly_loss_pct:.2f}% از سقف ۵٪)"

    return True, "نرمال"


# ==================== دریافت داده‌های بازار و کندل‌ها از نوبیتکس ====================
def get_all_active_markets(limit=180):
    """دریافت بیش از ۱۶۰ بازار فعال نوبیتکس با حذف ۱۰۰٪ استیبل‌کوین‌ها"""
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

            spread_pct = ((best_sell - best_buy) / best_buy) * 100.0
            norm_vol = volume_dst if quote_coin == "USDT" else (volume_dst / 60000.0)

            market_info[symbol] = {
                "base": base_coin,
                "quote": quote_coin,
                "spread": spread_pct,
                "volume": norm_vol,
                "latest": latest
            }

            if norm_vol >= 600:
                candidates.append((symbol, norm_vol))

        candidates.sort(key=lambda x: x[1], reverse=True)
        top_list = [item[0] for item in candidates[:limit]]
        if len(top_list) >= 20:
            return top_list, market_info

    fallback = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TONUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "SHIBUSDT", "TRXUSDT"]
    return fallback, market_info


def get_nobitex_candles(symbol, count=260):
    """دریافت کندل‌های ۱۵ دقیقه‌ای کافی برای EMA 200 و بازسازی تایم‌فریم‌های ۱ ساعته و ۴ ساعته"""
    to_time = int(time.time())
    from_time = to_time - (count * 15 * 60)
    endpoint = f"/market/udf/history?symbol={symbol}&resolution=15&from={from_time}&to={to_time}"
    res = request_nobitex(endpoint, timeout=8)
    if res and res.get("s") == "ok" and len(res.get("c", [])) >= 60:
        return pd.DataFrame({
            "time": pd.Series(res["t"], dtype=int),
            "open": pd.Series(res["o"], dtype=float),
            "high": pd.Series(res["h"], dtype=float),
            "low": pd.Series(res["l"], dtype=float),
            "close": pd.Series(res["c"], dtype=float),
            "volume": pd.Series(res.get("v", [1.0] * len(res["c"])), dtype=float)
        })
    return None


# ==================== محاسبات اندیکاتورهای تکنیکال و ساختار بازار (بند ۱ و ۲) ====================
def compute_all_indicators(df):
    """محاسبه دقیق EMA 20/50/200, RSI 14, MACD, ATR 14, ADX 14, VWAP, OBV و ساختار سقف/کف"""
    c = df["close"]
    h = df["high"]
    l = df["low"]
    o = df["open"]
    v = df["volume"].replace(0, 1e-8)

    # 1. EMA 20, 50, 200
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()

    # 2. Wilder's RSI 14
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean().replace(0, 1e-10)
    rs = avg_gain / avg_loss
    rsi = (100 - (100 / (1 + rs))).fillna(50.0)

    # 3. MACD (12, 26, 9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal

    # 4. ATR 14
    prev_c = c.shift(1)
    tr = pd.concat([
        (h - l),
        (h - prev_c).abs(),
        (l - prev_c).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    atr_pct = (atr / c * 100.0).fillna(1.0)

    # 5. ADX 14 (+DI, -DI, ADX)
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr_smooth = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean().replace(0, 1e-10)
    plus_di = 100.0 * (plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / tr_smooth)
    minus_di = 100.0 * (minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / tr_smooth)
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)).fillna(0.0)
    adx = dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean().fillna(15.0)

    # 6. Rolling 24h VWAP (96 candles of 15m)
    typical_price = (h + l + c) / 3.0
    tp_v = typical_price * v
    vwap = (tp_v.rolling(window=96, min_periods=10).sum() / v.rolling(window=96, min_periods=10).sum()).fillna(c)

    # 7. OBV & Relative Volume (RVOL)
    direction = np.sign(c.diff()).fillna(0.0)
    obv = (direction * v).cumsum()
    obv_ema10 = obv.ewm(span=10, adjust=False).mean()
    vol_ma20 = v.rolling(window=20, min_periods=5).mean().replace(0, 1e-8)
    rvol = (v / vol_ma20).fillna(1.0)

    # 8. تایم‌فریم‌های میان‌مدت (1H) و بلندمدت (4H)
    htf_1h_close = c.groupby(df.index // 4).last()
    htf_1h_ema20 = htf_1h_close.ewm(span=20, adjust=False).mean()
    htf_1h_ema50 = htf_1h_close.ewm(span=50, adjust=False).mean()
    med_term_bullish = bool(htf_1h_close.iloc[-1] >= htf_1h_ema20.iloc[-1] * 0.995 or htf_1h_ema20.iloc[-1] >= htf_1h_ema50.iloc[-1])

    htf_4h_close = c.groupby(df.index // 16).last()
    htf_4h_ema10 = htf_4h_close.ewm(span=10, adjust=False).mean()
    long_term_bullish = bool(htf_4h_close.iloc[-1] >= htf_4h_ema10.iloc[-1] * 0.99)

    # 9. سطوح حمایت و مقاومت ساختاری (Support / Resistance) و Swing High/Low
    recent_60_h = h.iloc[-60:]
    recent_60_l = l.iloc[-60:]
    resistance_level = float(recent_60_h.quantile(0.95))
    support_level = float(recent_60_l.quantile(0.08))
    swing_low_15 = float(l.iloc[-15:].min())

    # بررسی ساختار کف‌های بالاتر (Higher Low)
    prev_swing_low = float(l.iloc[-35:-15].min()) if len(l) >= 35 else swing_low_15
    higher_low_structure = bool(swing_low_15 >= prev_swing_low * 0.992)

    # 10. الگوی پرایس‌اکشن کندل ورود (Bullish Pinbar / Hammer / Engulfing)
    last_o, last_c, last_h, last_l = float(o.iloc[-1]), float(c.iloc[-1]), float(h.iloc[-1]), float(l.iloc[-1])
    prev_o, prev_c_val = float(o.iloc[-2]), float(c.iloc[-2])
    candle_range = max(last_h - last_l, 1e-10)
    lower_wick = min(last_o, last_c) - last_l
    is_pinbar = (lower_wick / candle_range >= 0.42) and (last_c >= last_o)
    is_engulfing = (last_c > last_o) and (prev_c_val < prev_o) and (last_c >= prev_o)
    bullish_price_action = bool(last_c > last_o or is_pinbar or is_engulfing)

    # 11. طبقه‌بندی رژیم بازار (Market Regime Classification)
    cur_adx = float(adx.iloc[-1])
    cur_atr_pct = float(atr_pct.iloc[-1])
    cur_c = float(c.iloc[-1])
    cur_ema50 = float(ema50.iloc[-1])
    cur_ema200 = float(ema200.iloc[-1])

    if cur_atr_pct >= 3.2:
        regime = "پرنوسان (High-Vol)"
    elif cur_adx >= 22.0 and cur_c > cur_ema50 >= cur_ema200 * 0.995:
        regime = "صعودی (Bullish)"
    elif cur_adx >= 22.0 and cur_c < cur_ema50 < cur_ema200:
        regime = "نزولی (Bearish)"
    else:
        regime = "خنثی/رنج (Range)"

    return {
        "close": cur_c,
        "prev_close": float(c.iloc[-2]),
        "high": last_h,
        "low": last_l,
        "ema20": float(ema20.iloc[-1]),
        "ema50": cur_ema50,
        "ema200": cur_ema200,
        "rsi": float(rsi.iloc[-1]),
        "prev_rsi": float(rsi.iloc[-2]),
        "prev2_rsi": float(rsi.iloc[-3]),
        "macd_line": float(macd_line.iloc[-1]),
        "macd_signal": float(macd_signal.iloc[-1]),
        "macd_hist": float(macd_hist.iloc[-1]),
        "prev_macd_hist": float(macd_hist.iloc[-2]),
        "atr": float(atr.iloc[-1]),
        "atr_pct": cur_atr_pct,
        "adx": cur_adx,
        "plus_di": float(plus_di.iloc[-1]),
        "minus_di": float(minus_di.iloc[-1]),
        "vwap": float(vwap.iloc[-1]),
        "obv_rising": bool(obv.iloc[-1] > obv_ema10.iloc[-1] or obv.iloc[-1] > obv.iloc[-3]),
        "rvol": float(rvol.iloc[-1]),
        "med_term_bullish": med_term_bullish,
        "long_term_bullish": long_term_bullish,
        "support": support_level,
        "resistance": resistance_level,
        "swing_low": swing_low_15,
        "higher_low": higher_low_structure,
        "bullish_pa": bullish_price_action,
        "is_pinbar_or_engulf": bool(is_pinbar or is_engulfing),
        "regime": regime,
        "returns_series": c.pct_change().dropna().tail(50)
    }


# ==================== اعتبارسنجی آماری Walk-Forward و Monte Carlo (بند ۳ و ۷) ====================
def validate_walk_forward_and_monte_carlo(df, target_rr=2.0):
    """
    بک‌تست سریع خارج از نمونه (Walk-Forward) و شبیه‌سازی مونت‌کارلو روی ۲۰۰ کندل اخیر همان ارز
    برای تخمین احتمال برد آماری و جلوگیری از ورود به ارزهای دارای رفتار تصادفی زیان‌ده
    """
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    if len(closes) < 80:
        return 0.45, True

    wins = 0
    trials = 0
    sim_outcomes = []

    for i in range(30, len(closes) - 12):
        # بررسی نقاط بازگشتی مشابه در گذشته نزدیک ارز
        ret_3 = (closes[i] - closes[i - 3]) / closes[i - 3]
        rebound = closes[i] > closes[i - 1]
        if ret_3 < -0.012 and rebound:
            entry = closes[i]
            local_sl = min(lows[i - 4:i + 1]) * 0.997
            risk = max(entry - local_sl, entry * 0.006)
            tp = entry + risk * target_rr

            future_h = highs[i + 1:i + 12]
            future_l = lows[i + 1:i + 12]
            hit_tp = np.any(future_h >= tp)
            hit_sl = np.any(future_l <= local_sl)

            if hit_tp and not hit_sl:
                wins += 1
                trials += 1
                sim_outcomes.append(target_rr)
            elif hit_sl:
                trials += 1
                sim_outcomes.append(-1.0)

    empirical_win_rate = (wins / trials) if trials >= 4 else 0.46

    # شبیه‌سازی مونت‌کارلو (۱۰۰ مسیر تصادفی) برای بررسی پایداری و ریسک افت
    mc_stable = True
    if len(sim_outcomes) >= 5:
        rng = random.Random(42)
        ruin_count = 0
        for _ in range(100):
            sample_path = [rng.choice(sim_outcomes) for _ in range(10)]
            if sum(sample_path) < -4.0:
                ruin_count += 1
        if ruin_count > 35:
            mc_stable = False

    return empirical_win_rate, mc_stable


# ==================== موتور امتیازدهی ۴ ستونی و امید ریاضی (بند ۳) ====================
def evaluate_opportunity_quant(df, ind, spread_pct, portfolio):
    """
    امتیازدهی ۰ تا ۱۰۰ از ۴ ستون مستقل + محاسبه نسبت ریسک به بازده و امید ریاضی خالص (Expectancy)
    """
    # فیلتر شرایط بحرانی (بند ۶): اسپرد زیاد، نوسان غیرعادی یا رژیم نزولی سنگین
    if spread_pct > MAX_SPREAD_PCT:
        return None
    if ind["atr_pct"] > 4.2 or ind["atr_pct"] < 0.35:
        return None
    if ind["regime"] == "نزولی (Bearish)" and ind["adx"] > 30.0:
        return None

    # --- ستون ۱: روند مولتی تایم‌فریم و ساختار بازار (حداکثر ۳۰ امتیاز) ---
    score_trend = 0.0
    if ind["long_term_bullish"]:
        score_trend += 10.0
    if ind["med_term_bullish"]:
        score_trend += 8.0
    if ind["close"] >= ind["ema200"] * 0.99:
        score_trend += 5.0
    if ind["higher_low"]:
        score_trend += 4.0
    if ind["plus_di"] >= ind["minus_di"] or ind["adx"] < 25.0:
        score_trend += 3.0

    # --- ستون ۲: موقعیت مکانی، حمایت/مقاومت و VWAP (حداکثر ۲۵ امتیاز) ---
    score_location = 0.0
    dist_to_support_pct = ((ind["close"] - ind["support"]) / ind["close"]) * 100.0
    dist_to_res_pct = ((ind["resistance"] - ind["close"]) / ind["close"]) * 100.0

    if 0.0 <= dist_to_support_pct <= 2.5:
        score_location += 13.0
    elif dist_to_support_pct <= 4.0:
        score_location += 8.0

    # بررسی فضای کافی تا سقف مقاومت برای تحقق R:R حداقل ۱:۲
    if dist_to_res_pct >= 2.2:
        score_location += 7.0
    if ind["close"] <= ind["vwap"] * 1.005:
        score_location += 5.0

    # --- ستون ۳: مومنتوم و ماشه پرایس‌اکشن (حداکثر ۲۵ امتیاز) ---
    score_momentum = 0.0
    was_oversold = (ind["rsi"] <= 36.0 or ind["prev_rsi"] <= 35.0 or ind["prev2_rsi"] <= 34.0)
    rsi_turning_up = (ind["rsi"] > ind["prev_rsi"] and 24.0 <= ind["rsi"] <= 46.0)
    macd_inflecting = (ind["macd_hist"] > ind["prev_macd_hist"])

    if was_oversold and rsi_turning_up:
        score_momentum += 12.0
    elif rsi_turning_up and ind["rsi"] <= 42.0:
        score_momentum += 7.0

    if macd_inflecting:
        score_momentum += 6.0
    if ind["bullish_pa"] and ind["close"] > ind["prev_close"]:
        score_momentum += 4.0
    if ind["is_pinbar_or_engulf"]:
        score_momentum += 3.0

    # --- ستون ۴: حجم و جریان پول هوشمند OBV (حداکثر ۲۰ امتیاز) ---
    score_volume = 0.0
    if ind["obv_rising"]:
        score_volume += 10.0
    if ind["rvol"] >= 1.15:
        score_volume += 10.0
    elif ind["rvol"] >= 0.85:
        score_volume += 6.0

    # قانون عدم تناقض: هیچ ستونی نباید صفر مطلق باشد و ماشه برگشت قیمت باید فعال باشد
    if not (ind["close"] > ind["prev_close"] and rsi_turning_up):
        return None
    if score_trend < 10.0 or score_location < 8.0 or score_momentum < 12.0 or score_volume < 6.0:
        return None

    total_score = score_trend + score_location + score_momentum + score_volume
    if total_score < MIN_SIGNAL_SCORE:
        return None

    # --- محاسبه حد ضرر ساختاری/ATR و حد سود (بند ۵) ---
    entry_price = ind["close"]
    atr_val = ind["atr"]
    structural_sl = min(ind["swing_low"], entry_price - (ATR_SL_MULTIPLIER * atr_val))
    raw_sl_pct = ((entry_price - structural_sl) / entry_price) * 100.0
    # محدود کردن فاصله حد ضرر بین ۰.۶۵٪ تا ۲.۲٪ برای کنترل دقیق ریسک
    sl_distance_pct = max(0.65, min(raw_sl_pct, 2.20))
    stop_loss_price = entry_price * (1.0 - sl_distance_pct / 100.0)

    # هدف قیمتی بر اساس مقاومت ساختاری یا حداقل ۲.۴ برابر ریسک
    structural_tp_pct = max(dist_to_res_pct * 0.90, sl_distance_pct * 2.40)
    take_profit_price = entry_price * (1.0 + structural_tp_pct / 100.0)

    # کسر هزینه‌های واقعی معامله (کارمزد + اسپرد)
    total_cost_pct = FEE_ROUNDTRIP_PCT + (spread_pct * 0.5)
    net_risk_pct = sl_distance_pct + total_cost_pct
    net_reward_pct = max(0.1, structural_tp_pct - total_cost_pct)
    net_rr_ratio = net_reward_pct / net_risk_pct

    if net_rr_ratio < MIN_RR_RATIO:
        return None

    # اعتبارسنجی آماری Walk-Forward و Monte Carlo
    wf_win_rate, mc_stable = validate_walk_forward_and_monte_carlo(df, target_rr=net_rr_ratio)
    if not mc_stable:
        return None

    # تخمین احتمال موفقیت ترکیبی (Bayesian Calibration)
    hist_trades = portfolio.get("total_trades", 0)
    hist_wr = (portfolio.get("win_trades", 0) / hist_trades) if hist_trades >= 5 else 0.48
    score_prob = 0.38 + ((total_score - 70.0) / 30.0) * 0.22
    est_win_prob = max(0.35, min(0.68, (0.45 * score_prob) + (0.35 * wf_win_rate) + (0.20 * hist_wr)))

    # محاسبه امید ریاضی خالص پس از کسر هزینه‌ها (بند ۳)
    expectancy_r = (est_win_prob * net_rr_ratio) - ((1.0 - est_win_prob) * 1.0)
    if expectancy_r < MIN_EXPECTANCY_R:
        return None

    return {
        "score": total_score,
        "regime": ind["regime"],
        "entry": entry_price,
        "stop_loss": stop_loss_price,
        "take_profit": take_profit_price,
        "sl_distance_pct": sl_distance_pct,
        "net_risk_pct": net_risk_pct,
        "net_reward_pct": net_reward_pct,
        "net_rr": net_rr_ratio,
        "win_prob": est_win_prob,
        "expectancy_r": expectancy_r,
        "atr": atr_val,
        "rsi": ind["rsi"],
        "cost_pct": total_cost_pct,
        "returns_series": ind["returns_series"]
    }


# ==================== محاسبه حجم معامله مبتنی بر ریسک و بررسی هم‌بستگی (بند ۴) ====================
def compute_position_size_and_check_risk(portfolio, current_equity, opp, open_returns_map):
    """
    تعیین حجم موقعیت بر اساس فاصله ورود تا حد ضرر (ریسک ۰.۵ تا ۱ درصد سرمایه)
    و بررسی عدم هم‌بستگی شدید با معاملات باز فعلی
    """
    # ۱. بررسی هم‌بستگی پیرسون با ارزهای موجود در سبد
    cand_ret = opp["returns_series"]
    for open_sym, open_ret in open_returns_map.items():
        if len(cand_ret) >= 25 and len(open_ret) >= 25:
            aligned = pd.concat([cand_ret, open_ret], axis=1).dropna()
            if len(aligned) >= 20:
                corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
                if pd.notna(corr) and corr > MAX_CORRELATION_LIMIT:
                    return None, f"هم‌بستگی بالا ({corr:.2f}) با {open_sym}"

    # ۲. بررسی سقف ریسک تجمعی کل سبد
    current_open_risk_usdt = sum(float(p.get("risk_usdt", 0.5)) for p in portfolio["positions"].values())
    max_open_risk_usdt = current_equity * (MAX_TOTAL_OPEN_RISK_PCT / 100.0)

    # تعیین درصد ریسک مجاز این معامله (بین ۰.۵٪ تا ۱.۰٪ بر اساس امتیاز سیگنال)
    risk_pct = MIN_RISK_PER_TRADE_PCT + ((opp["score"] - MIN_SIGNAL_SCORE) / (100.0 - MIN_SIGNAL_SCORE)) * (MAX_RISK_PER_TRADE_PCT - MIN_RISK_PER_TRADE_PCT)
    risk_pct = max(MIN_RISK_PER_TRADE_PCT, min(MAX_RISK_PER_TRADE_PCT, risk_pct))
    target_risk_usdt = current_equity * (risk_pct / 100.0)

    if current_open_risk_usdt + target_risk_usdt > max_open_risk_usdt:
        return None, "پر شدن سقف ریسک تجمعی سبد (۲٪)"

    # ۳. محاسبه حجم پوزیشن از روی فاصله حد ضرر
    raw_pos_size = target_risk_usdt / (opp["net_risk_pct"] / 100.0)
    clamped_pos_size = max(MIN_POS_SIZE_USDT, min(MAX_POS_SIZE_USDT, raw_pos_size))

    if portfolio["balance"] < clamped_pos_size:
        return None, "موجودی نقد ناکافی"

    # اطمینان از اینکه پس از محدودسازی حجم، ریسک واقعی هرگز از ۱.۰۵٪ کل سرمایه تجاوز نکند
    actual_risk_usdt = clamped_pos_size * (opp["net_risk_pct"] / 100.0)
    if actual_risk_usdt > current_equity * (MAX_RISK_PER_TRADE_PCT * 1.05 / 100.0):
        return None, "ریسک بیش از ۱٪ سرمایه در حداقل حجم سفارش"

    return {
        "pos_size_usdt": round(clamped_pos_size, 2),
        "risk_usdt": round(actual_risk_usdt, 3),
        "risk_pct_equity": round((actual_risk_usdt / current_equity) * 100.0, 2)
    }, "OK"


# ==================== بررسی شرایط بحرانی کل بازار (بند ۶) ====================
def check_global_market_crisis():
    btc_df = get_nobitex_candles("BTCUSDT", count=100)
    if btc_df is None or len(btc_df) < 30:
        return True, 0.0, 0.0, "نرمال"
    ind = compute_all_indicators(btc_df)
    closes = btc_df["close"]
    cur_btc = float(closes.iloc[-1])
    btc_45m = float(closes.iloc[-4])
    change_45m = ((cur_btc - btc_45m) / btc_45m) * 100.0

    # تشخیص سقوط ناگهانی یا نوسان غیرعادی در لیدر بازار
    if change_45m <= -1.3 and cur_btc < float(closes.iloc[-2]):
        return False, cur_btc, change_45m, f"ریزش تند بیت‌کوین ({change_45m:+.2f}%)"
    if ind["atr_pct"] >= 3.5:
        return False, cur_btc, change_45m, f"نوسان غیرعادی بازار (ATR: {ind['atr_pct']:.1f}%)"

    return True, cur_btc, change_45m, ind["regime"]


# ==================== محاسبه ۱۰ شاخص ارزیابی عملکرد (بند ۷) ====================
def compute_performance_metrics(portfolio, current_equity):
    net_profit = current_equity - INITIAL_CAPITAL
    net_profit_pct = (net_profit / INITIAL_CAPITAL) * 100.0
    total_trades = portfolio.get("total_trades", 0)
    win_trades = portfolio.get("win_trades", 0)
    loss_trades = max(0, total_trades - win_trades)
    win_rate = (win_trades / total_trades * 100.0) if total_trades > 0 else 0.0

    gross_p = float(portfolio.get("gross_profit", 0.0))
    gross_l = abs(float(portfolio.get("gross_loss", 0.0)))
    profit_factor = (gross_p / gross_l) if gross_l > 0 else (gross_p if gross_p > 0 else 0.0)

    avg_win = (gross_p / win_trades) if win_trades > 0 else 0.0
    avg_loss = (gross_l / loss_trades) if loss_trades > 0 else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    r_list = portfolio.get("trade_r_multiples", [])
    expectancy_r = float(np.mean(r_list)) if r_list else 0.0
    expectancy_usdt = ((gross_p - gross_l) / total_trades) if total_trades > 0 else 0.0

    ret_list = portfolio.get("trade_returns_pct", [])
    if len(ret_list) >= 3:
        arr = np.array(ret_list, dtype=float)
        mean_r = np.mean(arr)
        std_r = np.std(arr)
        downside_arr = arr[arr < 0]
        downside_std = np.std(downside_arr) if len(downside_arr) >= 2 else (abs(downside_arr[0]) if len(downside_arr) == 1 else 1e-6)
        sharpe = (mean_r / std_r) * math.sqrt(len(arr)) if std_r > 1e-6 else 0.0
        sortino = (mean_r / downside_std) * math.sqrt(len(arr)) if downside_std > 1e-6 else 0.0
    else:
        sharpe, sortino = 0.0, 0.0

    return {
        "net_profit": net_profit,
        "net_profit_pct": net_profit_pct,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "expectancy_r": expectancy_r,
        "expectancy_usdt": expectancy_usdt,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": portfolio.get("max_drawdown_pct", 0.0),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "total_trades": total_trades,
        "total_fees": portfolio.get("total_fees_paid", 0.0)
    }


# ==================== اجرای یک دور کامل اسکن و مدیریت سبد ====================
def execute_single_scan_cycle(portfolio, cycle_num, start_ts, dashboard_msg_id):
    positions = portfolio["positions"]
    cooldowns = portfolio.get("cooldowns", {})
    now_ts = int(time.time())

    cooldowns = {k: v for k, v in cooldowns.items() if now_ts < v}
    portfolio["cooldowns"] = cooldowns

    market_safe, btc_price, btc_change, btc_regime = check_global_market_crisis()
    top_symbols, market_info = get_all_active_markets(MAX_COINS_TO_SCAN)
    all_symbols = list(dict.fromkeys(list(positions.keys()) + top_symbols))

    latest_prices = {sym: info["latest"] for sym, info in market_info.items()}
    current_equity = calculate_total_equity(portfolio, latest_prices)
    risk_allowed, breaker_status = update_risk_watermarks_and_check_breakers(portfolio, current_equity)

    buy_candidates = []
    scanned_summary = []
    open_returns_map = {}

    for symbol in all_symbols:
        df = get_nobitex_candles(symbol, count=240)
        if df is None:
            time.sleep(0.05)
            continue

        ind = compute_all_indicators(df)
        current_price = ind["close"]
        latest_prices[symbol] = current_price

        scanned_summary.append({
            "symbol": symbol,
            "price": current_price,
            "rsi": ind["rsi"],
            "regime": ind["regime"]
        })

        # ==================== ۱. مدیریت معاملات باز (بند ۵ دستورالعمل) ====================
        if symbol in positions:
            open_returns_map[symbol] = ind["returns_series"]
            pos = positions[symbol]
            buy_price = float(pos["buy_price"])
            invested = float(pos["invested"])
            sl_price = float(pos["stop_loss"])
            tp_price = float(pos["take_profit"])
            initial_risk_pct = max(float(pos.get("sl_distance_pct", 1.0)), 0.4)
            buy_time = int(pos.get("buy_time", now_ts))
            hours_held = (now_ts - buy_time) / 3600.0

            peak_candidate = max(current_price, ind["high"]) if (now_ts - buy_time >= 600) else current_price
            highest_price = max(float(pos.get("highest_price", buy_price)), peak_candidate)
            pos["highest_price"] = highest_price

            gross_profit_pct = ((current_price - buy_price) / buy_price) * 100.0
            max_profit_pct = ((highest_price - buy_price) / buy_price) * 100.0
            current_r_multiple = gross_profit_pct / initial_risk_pct
            max_r_multiple = max_profit_pct / initial_risk_pct

            spread_now = market_info.get(symbol, {}).get("spread", 0.25)
            cost_pct = FEE_ROUNDTRIP_PCT + (spread_now * 0.5)
            net_profit_pct = gross_profit_pct - cost_pct
            net_profit_usdt = invested * (net_profit_pct / 100.0)
            fee_usdt = invested * (cost_pct / 100.0)

            # الف) انتقال خودکار حد ضرر به نقطه سربه‌سر پس از رسیدن به شرایط (0.75R)
            if max_r_multiple >= BREAKEVEN_TRIGGER_R and not pos.get("breakeven_active", False):
                pos["breakeven_active"] = True
                be_price = buy_price * (1.0 + (cost_pct + 0.15) / 100.0)
                if be_price > sl_price:
                    pos["stop_loss"] = be_price
                    sl_price = be_price

            # ب) حد سود متحرک پویا مبتنی بر ATR (Chandelier Trailing Stop)
            if max_r_multiple >= TRAILING_ACTIVATE_R:
                chandelier_sl = highest_price - (ATR_TRAIL_MULTIPLIER * ind["atr"])
                if chandelier_sl > sl_price:
                    pos["stop_loss"] = chandelier_sl
                    sl_price = chandelier_sl

            should_sell = False
            sell_reason = ""
            is_stop_loss = False

            if current_price <= sl_price:
                should_sell = True
                if pos.get("breakeven_active", False) and net_profit_pct >= 0:
                    sell_reason = f"خروج در حد سود متحرک / سربه‌سر ({max_r_multiple:.1f}R لمس شد)"
                else:
                    is_stop_loss = True
                    sell_reason = "برخورد به حد ضرر ساختاری/ATR"
            elif current_price >= tp_price and ind["rsi"] >= 72.0:
                should_sell = True
                sell_reason = f"رسیدن به هدف قیمتی R:R و اشباع خرید (RSI: {ind['rsi']:.1f})"
            elif hours_held >= STAGNANT_HOURS and net_profit_pct >= 0.10 and ind["rsi"] >= 50.0:
                should_sell = True
                sell_reason = f"خروج زمانی از معامله راکد پس از {hours_held:.1f} ساعت"

            if should_sell:
                portfolio["balance"] += (invested + net_profit_usdt)
                portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
                portfolio["total_fees_paid"] = portfolio.get("total_fees_paid", 0.0) + fee_usdt
                realized_r = net_profit_pct / initial_risk_pct
                portfolio.setdefault("trade_returns_pct", []).append(round(net_profit_pct, 3))
                portfolio.setdefault("trade_r_multiples", []).append(round(realized_r, 3))

                if net_profit_usdt >= 0:
                    portfolio["win_trades"] = portfolio.get("win_trades", 0) + 1
                    portfolio["gross_profit"] = portfolio.get("gross_profit", 0.0) + net_profit_usdt
                else:
                    portfolio["gross_loss"] = portfolio.get("gross_loss", 0.0) + abs(net_profit_usdt)

                if is_stop_loss:
                    portfolio["cooldowns"][symbol] = now_ts + COOLDOWN_SECONDS

                del portfolio["positions"][symbol]
                total_eq = calculate_total_equity(portfolio, latest_prices)
                metrics = compute_performance_metrics(portfolio, total_eq)

                msg = (
                    f"⭕️ بسته شدن معامله الگوریتمی ({symbol}) | {get_tehran_time_str()}\n\n"
                    f"✅ علت خروج: {sell_reason}\n"
                    f"✅ قیمت ورود: {format_price(buy_price)} | خروج: {format_price(current_price)}\n"
                    f"✅ بازده خالص: {net_profit_pct:+.2f}% ({net_profit_usdt:+.2f} USDT | {realized_r:+.2f}R)\n"
                    f"✅ شاخص سودآوری (PF): {metrics['profit_factor']:.2f} | نرخ برد: {metrics['win_rate']:.0f}%\n"
                    f"🛑 ارزش کل سبد: {total_eq:.2f} USDT"
                )
                send_telegram(msg)

        # ==================== ۲. ارزیابی فرصت‌های جدید با موتور تصمیم‌گیری (بند ۳) ====================
        elif market_safe and risk_allowed and len(positions) < MAX_OPEN_POSITIONS:
            base_coin = market_info.get(symbol, {}).get("base", symbol.replace("USDT", "").replace("IRT", ""))
            if base_coin in STABLECOINS_BLACKLIST or symbol in cooldowns:
                continue

            already_holding_base = any(
                market_info.get(open_sym, {}).get("base") == base_coin for open_sym in positions.keys()
            )
            if already_holding_base:
                continue

            spread_pct = market_info.get(symbol, {}).get("spread", 1.0)
            opp = evaluate_opportunity_quant(df, ind, spread_pct, portfolio)
            if opp is not None:
                opp["symbol"] = symbol
                buy_candidates.append(opp)

        time.sleep(0.05)

    # ==================== ۳. اولویت‌بندی بر اساس امید ریاضی و ثبت سفارش ====================
    buy_candidates.sort(key=lambda x: (x["expectancy_r"] * x["score"]), reverse=True)
    buys_done = 0

    for cand in buy_candidates:
        if buys_done >= MAX_BUYS_PER_CYCLE or len(portfolio["positions"]) >= MAX_OPEN_POSITIONS:
            break

        current_equity = calculate_total_equity(portfolio, latest_prices)
        sizing, reason = compute_position_size_and_check_risk(portfolio, current_equity, cand, open_returns_map)
        if sizing is None:
            continue

        sym = cand["symbol"]
        pos_usdt = sizing["pos_size_usdt"]
        portfolio["balance"] -= pos_usdt
        portfolio["positions"][sym] = {
            "buy_price": cand["entry"],
            "invested": pos_usdt,
            "stop_loss": cand["stop_loss"],
            "take_profit": cand["take_profit"],
            "sl_distance_pct": cand["sl_distance_pct"],
            "risk_usdt": sizing["risk_usdt"],
            "highest_price": cand["entry"],
            "breakeven_active": False,
            "score": cand["score"],
            "expectancy_r": cand["expectancy_r"],
            "buy_time": now_ts
        }
        open_returns_map[sym] = cand["returns_series"]
        buys_done += 1
        total_eq = calculate_total_equity(portfolio, latest_prices)

        msg = (
            f"⭕️ ورود الگوریتمی جدید ({sym}) | {get_tehran_time_str()}\n\n"
            f"✅ رژیم بازار: {cand['regime']} | امتیاز سیگنال: {cand['score']:.1f}/100\n"
            f"✅ قیمت ورود: {format_price(cand['entry'])}\n"
            f"✅ حد ضرر (SL): {format_price(cand['stop_loss'])} | هدف (TP): {format_price(cand['take_profit'])}\n"
            f"✅ نسبت R:R خالص: 1:{cand['net_rr']:.2f} | احتمال برد: {cand['win_prob']*100:.0f}%\n"
            f"✅ امید ریاضی (Expectancy): +{cand['expectancy_r']:.2f}R\n"
            f"✅ حجم موقعیت: {pos_usdt:.2f} USDT (ریسک معامله: {sizing['risk_usdt']:.2f}$ معادل {sizing['risk_pct_equity']}% سرمایه)\n"
            f"🛑 ارزش کل دارایی: {total_eq:.2f} USDT"
        )
        send_telegram(msg)

    save_portfolio(portfolio)

    # ==================== ۴. بروزرسانی داشبورد زنده کوانت در تلگرام (بند ۷) ====================
    if scanned_summary:
        total_eq = calculate_total_equity(portfolio, latest_prices)
        metrics = compute_performance_metrics(portfolio, total_eq)
        scanned_summary.sort(key=lambda x: x["rsi"])
        watch_list = " | ".join([f"{c['symbol']}: {c['rsi']:.1f}" for c in scanned_summary[:4]])

        pos_lines = []
        for sym, pos in portfolio["positions"].items():
            cur_p = latest_prices.get(sym, float(pos["buy_price"]))
            buy_p = float(pos["buy_price"])
            pnl = ((cur_p - buy_p) / buy_p) * 100.0 - FEE_ROUNDTRIP_PCT
            st_badge = "🛡️سربه‌سر" if pos.get("breakeven_active") else f"SL:{format_price(pos['stop_loss'])}"
            pos_lines.append(f"   🔹 {sym} ({pos['invested']:.0f}$): {pnl:+.2f}% [{st_badge}]")

        pos_block = "\n".join(pos_lines) if pos_lines else "   🔹 معامله بازی نیست (در انتظار فرصت با امید ریاضی مثبت)"
        mkt_str = f"🟢 {btc_regime} (BTC: {format_price(btc_price)}$)" if market_safe else f"🔴 بحرانی: {btc_regime}"
        risk_str = f"🟢 فعال ({breaker_status})" if risk_allowed else f"🔴 متوقف ({breaker_status})"

        elapsed_min = int((time.time() - start_ts) / 60)
        rem_min = max(0, 50 - elapsed_min)

        live_text = (
            f"⭕️ داشبورد زنده موتور تصمیم‌گیری کمّی نوبیتکس\n"
            f"⏱ بروزرسانی: {get_tehran_time_str()} | دور: #{cycle_num} ({rem_min} دقیقه تا تمدید)\n\n"
            f"✅ وضعیت کلان بازار: {mkt_str}\n"
            f"✅ وضعیت فیوزهای ریسک: {risk_str}\n"
            f"✅ بازارهای پایش‌شده (بدون استیبل‌کوین): {len(scanned_summary)} ارز\n"
            f"✅ کمترین RSI لحظه‌ای: {watch_list}\n\n"
            f"✅ موقعیت‌های باز فعلی:\n{pos_block}\n\n"
            f"📊 شاخص‌های ارزیابی عملکرد (بند ۷):\n"
            f"   • سود خالص (Net Profit): {metrics['net_profit']:+.2f} USDT ({metrics['net_profit_pct']:+.2f}%)\n"
            f"   • فاکتور سود (Profit Factor): {metrics['profit_factor']:.2f} | نرخ برد: {metrics['win_rate']:.1f}%\n"
            f"   • امید ریاضی (Expectancy): {metrics['expectancy_r']:+.2f}R ({metrics['expectancy_usdt']:+.2f}$)\n"
            f"   • نسبت شارپ: {metrics['sharpe']:.2f} | سورتینو: {metrics['sortino']:.2f}\n"
            f"   • حداکثر افت (Max DD): {metrics['max_dd']:.2f}% | میانگین برد/باخت: {metrics['payoff_ratio']:.2f}\n"
            f"   • کل معاملات: {metrics['total_trades']} | کل کارمزدها: {metrics['total_fees']:.2f}$\n\n"
            f"🛑 موجودی نقد: {portfolio['balance']:.2f}$ | ارزش کل سبد: {total_eq:.2f} USDT"
        )

        if dashboard_msg_id:
            if not edit_telegram(dashboard_msg_id, live_text):
                dashboard_msg_id = send_telegram(live_text)
        else:
            dashboard_msg_id = send_telegram(live_text)

    return dashboard_msg_id


def run_continuous_50m_engine():
    start_ts = time.time()
    portfolio = load_portfolio()

    init_text = (
        f"⭕️ موتور الگوریتمی کمّی (Quant Engine) روشن شد | {get_tehran_time_str()}\n\n"
        f"✅ در حال پایش بیش از ۱۶۰ ارز نوبیتکس بر اساس ۸ بند دستورالعمل...\n"
        f"✅ داشبورد زنده پس از اتمام اولین دور تحلیل در همین پیام ظاهر می‌شود."
    )
    dashboard_msg_id = send_telegram(init_text)

    cycle = 1
    while (time.time() - start_ts) < RUN_DURATION_SECONDS:
        cycle_start = time.time()
        print(f"--- شروع دور #{cycle} پایش کمّی بازار ---")
        try:
            dashboard_msg_id = execute_single_scan_cycle(portfolio, cycle, start_ts, dashboard_msg_id)
        except Exception as e:
            print(f"خطا در چرخه {cycle}: {e}")

        cycle += 1
        elapsed = time.time() - start_ts
        if elapsed + SCAN_INTERVAL_SECONDS >= RUN_DURATION_SECONDS:
            break

        sleep_time = max(15, SCAN_INTERVAL_SECONDS - int(time.time() - cycle_start))
        time.sleep(sleep_time)

    save_portfolio(portfolio)


if __name__ == "__main__":
    run_continuous_50m_engine()
