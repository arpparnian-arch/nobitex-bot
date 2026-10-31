import os
import json
import time
import requests
import pandas as pd

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TONUSDT", "DOGEUSDT"]

TAKE_PROFIT_PERCENT = 3.0
STOP_LOSS_PERCENT = -2.0
TRADE_AMOUNT = 20.0
PORTFOLIO_FILE = "portfolio.json"

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"خطا در ارسال تلگرام: {e}")

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    
    # اگر اولین بار است که ربات روشن می‌شود، پیام خوش‌آمدگویی بفرستد
    initial_data = {"balance": 100.0, "positions": {}}
    send_telegram(
        "⭕️ ربات معامله‌گر نوبیتکس با موفقیت روشن شد!\n\n"
        "✅ موجودی اولیه مجازی: ۱۰۰ تتر\n"
        "✅ ارزهای تحت نظر: BTC, ETH, SOL, TON, DOGE\n"
        "✅ استراتژی: خرید در کف قیمتی (RSI زیر ۳۲) | فروش با ۳٪ سود یا ۲٪ حد ضرر\n\n"
        "🛑 از این لحظه بازار هر ۱۵ دقیقه به صورت خودکار تحلیل می‌شود."
    )
    return initial_data

def save_portfolio(data):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_nobitex_candles(symbol):
    to_time = int(time.time())
    from_time = to_time - (60 * 15 * 60)
    url = f"https://api.nobitex.ir/market/udf/history?symbol={symbol}&resolution=15&from={from_time}&to={to_time}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get("s") == "ok":
            return pd.Series(res["c"], dtype=float)
    except Exception as e:
        print(f"خطا در دریافت اطلاعات {symbol}: {e}")
    return None

def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def run_bot():
    portfolio = load_portfolio()
    balance = portfolio["balance"]
    positions = portfolio["positions"]
    
    for symbol in SYMBOLS:
        prices = get_nobitex_candles(symbol)
        if prices is None or len(prices) < 20:
            continue
            
        current_price = prices.iloc[-1]
        rsi = calculate_rsi(prices)
        print(f"{symbol} -> Price: {current_price} | RSI: {rsi:.1f}")
        
        if symbol in positions:
            buy_price = positions[symbol]["buy_price"]
            amount = positions[symbol]["amount"]
            profit_pct = ((current_price - buy_price) / buy_price) * 100
            
            if profit_pct >= TAKE_PROFIT_PERCENT or profit_pct <= STOP_LOSS_PERCENT:
                sell_value = amount * current_price
                profit_usdt = sell_value - (amount * buy_price)
                portfolio["balance"] += sell_value
                del portfolio["positions"][symbol]
                
                status = "سود (Take Profit)" if profit_pct > 0 else "حد ضرر (Stop Loss)"
                msg = (
                    f"⭕️ گزارش فروش خودکار ({status})\n\n"
                    f"✅ ارز: {symbol}\n"
                    f"✅ قیمت خرید: {buy_price} تتر\n"
                    f"✅ قیمت فروش: {current_price} تتر\n"
                    f"✅ میزان سود/زیان: {profit_pct:.2f}% ({profit_usdt:.2f} تتر)\n"
                    f"✅ موجودی کل فعلی: {portfolio['balance']:.2f} تتر"
                )
                send_telegram(msg)
                
        else:
            if rsi < 32 and balance >= TRADE_AMOUNT:
                amount = TRADE_AMOUNT / current_price
                portfolio["balance"] -= TRADE_AMOUNT
                portfolio["positions"][symbol] = {
                    "buy_price": current_price,
                    "amount": amount
                }
                msg = (
                    f"⭕️ خرید جدید توسط ربات معامله‌گر\n\n"
                    f"✅ ارز: {symbol}\n"
                    f"✅ قیمت ورود: {current_price} تتر\n"
                    f"✅ شاخص RSI لحظه خرید: {rsi:.1f} (کف قیمتی)\n"
                    f"✅ مبلغ سرمایه‌گذاری: {TRADE_AMOUNT} تتر\n"
                    f"✅ موجودی نقد باقی‌مانده: {portfolio['balance']:.2f} تتر"
                )
                send_telegram(msg)

    save_portfolio(portfolio)

if __name__ == "__main__":
    run_bot()
