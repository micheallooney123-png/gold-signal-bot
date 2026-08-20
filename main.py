import os
import logging
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from flask import Flask
from waitress import serve
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ============================================================
# Environment variables (set these in Render -> Environment)
# BOT_TOKEN = 8600613901:AAFfH9TJWQxKx_md3iflKE0MN2xQXrQpZbg
# CHAT_ID   = 6532633465
#
# IMPORTANT: No paid market-data API key is required.
# Market data is fetched from Yahoo Finance's public chart endpoint.
# ============================================================
BOT_TOKEN = "8600613901:AAFfH9TJWQxKx_md3iflKE0MN2xQXrQpZbg"
CHAT_ID = "6532633465"

# Yahoo Finance gold futures symbol (USD/oz)
SYMBOL = "GC=F"
TIMEFRAME = "1hour"
HIGHER_TIMEFRAME = "4hour"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("GoldSignalBot")

# ------------------------------------------------------------
# Flask health endpoint for Render
# ------------------------------------------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Gold Signal Bot is Running"


def run_flask():
    port = int(os.getenv("PORT", "8080"))
    # Use Waitress instead of Flask's development server on Render.
    serve(app, host="0.0.0.0", port=port)


def keep_alive():
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()


# ------------------------------------------------------------
# Free market-data helpers (Yahoo Finance public chart endpoint)
# ------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (GoldSignalBot/3.0)",
    "Accept": "application/json",
})


def _yahoo_chart(symbol: str, interval: str, range_: str) -> dict:
    """Fetch public Yahoo Finance chart data; no API key is required."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = SESSION.get(
        url,
        params={
            "range": range_,
            "interval": interval,
            "includePrePost": "true",
            "events": "div,splits",
        },
        timeout=20,
    )
    response.raise_for_status()

    payload = response.json()
    result = payload.get("chart", {}).get("result")
    error = payload.get("chart", {}).get("error")

    if error:
        raise RuntimeError(
            f"Yahoo Finance error: {error.get('description') or error.get('code')}"
        )
    if not result:
        raise RuntimeError("Yahoo Finance returned no chart data")

    return result[0]


def _chart_to_ohlc(chart: dict) -> pd.DataFrame:
    """Convert Yahoo chart JSON to our standard OHLC DataFrame."""
    timestamps = chart.get("timestamp") or []
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]

    if not timestamps or not quote:
        raise RuntimeError("Yahoo Finance returned empty OHLC data")

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "close": quote.get("close"),
    })

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return (
        df.dropna(subset=["timestamp", "open", "high", "low", "close"])
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )


def _download_gold_1h(days: int) -> pd.DataFrame:
    """Download free 1-hour gold futures data from Yahoo Finance."""
    chart = _yahoo_chart(SYMBOL, "1h", "30d")
    df = _chart_to_ohlc(chart)

    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=int(days))
    df = df[df["timestamp"] >= cutoff].reset_index(drop=True)

    if len(df) < 80:
        raise RuntimeError(f"Not enough Yahoo Finance 1H gold candles: {len(df)}")

    return df


def get_ohlc(timeframe: str, days: int) -> pd.DataFrame:
    """Get gold OHLC candles from Yahoo Finance without a paid API."""
    h1 = _download_gold_1h(days)

    if timeframe == "1hour":
        return h1

    if timeframe == "4hour":
        x = h1.set_index("timestamp")[["open", "high", "low", "close"]]
        h4 = x.resample("4h", label="right", closed="right").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }).dropna().reset_index()

        if len(h4) < 80:
            raise RuntimeError(f"Not enough Yahoo Finance 4H gold candles: {len(h4)}")
        return h4

    raise ValueError(f"Unsupported timeframe: {timeframe}")


def get_live_quote():
    """Get the latest free gold price from Yahoo Finance."""
    chart = _yahoo_chart(SYMBOL, "5m", "5d")
    df = _chart_to_ohlc(chart)

    if df.empty:
        raise RuntimeError("Yahoo Finance returned no live gold quote")

    price = float(df.iloc[-1]["close"])
    if price <= 0:
        raise RuntimeError("Yahoo Finance returned an invalid gold price")

    meta = chart.get("meta") or {}
    previous_close = pd.to_numeric(meta.get("previousClose"), errors="coerce")
    if pd.isna(previous_close) or float(previous_close) <= 0:
        change_pct = None
    else:
        change_pct = (price - float(previous_close)) / float(previous_close) * 100

    return price, {
        "price": price,
        "changesPercentage": change_pct,
    }

def get_gold_data():
    """Get 1H and 4H gold data for multi-timeframe confirmation."""
    h1 = get_ohlc(TIMEFRAME, days=14)
    h4 = get_ohlc(HIGHER_TIMEFRAME, days=30)

    # The last intraday candle may still be forming. Exclude it so signals
    # are based on a completed candle and do not repaint while the candle moves.
    if len(h1) > 2:
        h1 = h1.iloc[:-1].copy()
    if len(h4) > 2:
        h4 = h4.iloc[:-1].copy()

    return h1, h4



# ------------------------------------------------------------
# Technical analysis
# ------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate EMA/RSI/ATR/MACD/ADX using pandas only (no paid API/library)."""
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # EMAs
    df["ema20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    df["ema50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    df["ema200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()

    # RSI(14) using Wilder-style exponential smoothing.
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    df["rsi"] = 100 - (100 / (1 + rs))
    df.loc[(avg_loss == 0) & (avg_gain > 0), "rsi"] = 100.0
    df.loc[(avg_gain == 0) & (avg_loss > 0), "rsi"] = 0.0

    # ATR(14)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    # MACD(12,26,9)
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ADX(14) with Wilder-style smoothing.
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[up_move > down_move] = up_move[up_move > down_move].clip(lower=0)
    minus_dm[down_move > up_move] = down_move[down_move > up_move].clip(lower=0)

    atr_safe = df["atr"].replace(0, pd.NA)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_safe
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    df["dmp"] = plus_di
    df["dmn"] = minus_di
    df["adx"] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    return df


def trend_from_higher_tf(df4: pd.DataFrame):
    d = add_indicators(df4)
    last = d.iloc[-1]

    if pd.isna(last["ema50"]) or pd.isna(last["ema200"]):
        return "UNKNOWN"

    if last["close"] > last["ema50"] > last["ema200"]:
        return "BULLISH"
    if last["close"] < last["ema50"] < last["ema200"]:
        return "BEARISH"
    return "NEUTRAL"


def analyze_signal(h1: pd.DataFrame, h4: pd.DataFrame, live_price: float):
    """Conservative multi-timeframe signal. No forced trade."""
    h1 = add_indicators(h1)
    h4 = add_indicators(h4)

    if len(h1) < 210 or len(h4) < 80:
        return None

    last = h1.iloc[-1]
    prev = h1.iloc[-2]
    higher = h4.iloc[-1]
    higher_trend = trend_from_higher_tf(h4)

    fields = [
        "ema20", "ema50", "ema200", "rsi", "atr",
        "macd", "macd_signal", "macd_hist", "adx", "dmp", "dmn"
    ]
    if any(pd.isna(last[f]) for f in fields):
        return None

    atr = float(last["atr"])
    if atr <= 0:
        return None

    # Prevent using a stale quote as an entry.
    candle_close = float(last["close"])
    deviation = abs(live_price - candle_close) / candle_close
    if deviation > 0.005:  # 0.5%
        logger.warning(
            "Live quote differs from completed candle by %.3f%%; no signal.",
            deviation * 100,
        )
        return None

    buy_score = 0
    sell_score = 0
    buy_reasons = []
    sell_reasons = []

    # 4H trend confirmation is mandatory.
    if higher_trend == "BULLISH":
        buy_score += 2
        buy_reasons.append("اتجاه 4H صاعد")
    elif higher_trend == "BEARISH":
        sell_score += 2
        sell_reasons.append("اتجاه 4H هابط")
    else:
        return None

    # 1H trend.
    if last["close"] > last["ema50"] > last["ema200"]:
        buy_score += 2
        buy_reasons.append("اتجاه 1H صاعد EMA50/200")
    elif last["close"] < last["ema50"] < last["ema200"]:
        sell_score += 2
        sell_reasons.append("اتجاه 1H هابط EMA50/200")

    # MACD direction.
    if last["macd"] > last["macd_signal"] and last["macd_hist"] > 0:
        buy_score += 1
        buy_reasons.append("MACD إيجابي")
    elif last["macd"] < last["macd_signal"] and last["macd_hist"] < 0:
        sell_score += 1
        sell_reasons.append("MACD سلبي")

    # RSI confirmation: avoid buying extreme overbought and selling extreme oversold.
    if 50 <= last["rsi"] <= 68:
        buy_score += 1
        buy_reasons.append(f"RSI {last['rsi']:.1f} مناسب للشراء")
    elif 32 <= last["rsi"] <= 50:
        sell_score += 1
        sell_reasons.append(f"RSI {last['rsi']:.1f} مناسب للبيع")

    # ADX: trend must have enough strength.
    if last["adx"] >= 20:
        if last["dmp"] > last["dmn"]:
            buy_score += 1
            buy_reasons.append(f"ADX {last['adx']:.1f} و +DI أعلى")
        elif last["dmn"] > last["dmp"]:
            sell_score += 1
            sell_reasons.append(f"ADX {last['adx']:.1f} و -DI أعلى")
    else:
        return None

    # EMA20 momentum / breakout confirmation.
    crossed_up = prev["close"] <= prev["ema20"] and last["close"] > last["ema20"]
    crossed_down = prev["close"] >= prev["ema20"] and last["close"] < last["ema20"]
    if crossed_up:
        buy_score += 1
        buy_reasons.append("اختراق EMA20")
    if crossed_down:
        sell_score += 1
        sell_reasons.append("كسر EMA20")

    # Require strong agreement and reject ties.
    if buy_score >= 6 and buy_score >= sell_score + 2:
        direction = "BUY"
        reasons = buy_reasons
        entry = live_price
        sl = entry - (atr * 1.5)
        tp1 = entry + (atr * 1.5)
        tp2 = entry + (atr * 3.0)
        score = buy_score
    elif sell_score >= 6 and sell_score >= buy_score + 2:
        direction = "SELL"
        reasons = sell_reasons
        entry = live_price
        sl = entry + (atr * 1.5)
        tp1 = entry - (atr * 1.5)
        tp2 = entry - (atr * 3.0)
        score = sell_score
    else:
        return None

    return {
        "direction": direction,
        "signal": "🟢 شراء (BUY)" if direction == "BUY" else "🔴 بيع (SELL)",
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "atr": atr,
        "rsi": float(last["rsi"]),
        "adx": float(last["adx"]),
        "score": score,
        "max_score": 8,
        "higher_trend": higher_trend,
        "reason": " | ".join(reasons),
        "candle_time": last["timestamp"],
        "data_source": "Yahoo Finance (GC=F)",
    }


def build_signal_message(result: dict, automatic=False):
    title = "🔔 إشارة تلقائية" if automatic else "⚡ إشارة الذهب"
    return (
        f"{title} — الذهب\n\n"
        f"{result['signal']}\n\n"
        f"💰 الدخول التقريبي: {result['price']:.2f}\n"
        f"🛑 وقف الخسارة: {result['sl']:.2f}\n"
        f"🎯 الهدف 1: {result['tp1']:.2f}\n"
        f"🎯 الهدف 2: {result['tp2']:.2f}\n\n"
        f"📊 قوة الإشارة: {result['score']}/{result['max_score']}\n"
        f"📈 اتجاه 4H: {result['higher_trend']}\n"
        f"RSI: {result['rsi']:.1f} | ADX: {result['adx']:.1f}\n"
        f"🧠 الأسباب: {result['reason']}\n\n"
        f"🕐 آخر شمعة مكتملة: {result['candle_time']}\n"
        f"📡 المصدر: {result['data_source']}\n\n"
        f"⚠️ هذه إشارة تحليلية وليست ضماناً للربح. تأكد من سعر وسيطك قبل التنفيذ."
    )


# ------------------------------------------------------------
# Telegram commands
# ------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Gold Signal Bot\n\n"
        "/signal - تحليل الذهب الآن\n"
        "/status - حالة الذهب\n"
        "/auto - إشارات تلقائية كل ساعة\n"
        "/stop - إيقاف الإشارات التلقائية\n"
        "/help - المساعدة"
    )


async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ أفحص بيانات الذهب وأتأكد من الاتجاه...")
    try:
        h1, h4 = get_gold_data()
        live_price, _ = get_live_quote()
        result = analyze_signal(h1, h4, live_price)
        if result:
            await update.message.reply_text(build_signal_message(result))
        else:
            await update.message.reply_text(
                "⏸️ لا توجد صفقة قوية الآن.\n\n"
                "تم رفض الإشارة لأن شروط الاتجاه/القوة/توافق الأطر الزمنية لم تجتمع."
            )
    except Exception as exc:
        logger.exception("Signal command failed")
        await update.message.reply_text(f"❌ تعذر تحليل الذهب حالياً.\nالسبب التقني: {exc}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        h1, _ = get_gold_data()
        live_price, quote = get_live_quote()
        d = add_indicators(h1)
        last = d.iloc[-1]
        trend = "صاعد 🟢" if last["ema50"] > last["ema200"] else "هابط 🔴"
        change = quote.get("changesPercentage")
        change_text = f"{float(change):+.2f}%" if change not in (None, "") else "غير متاح"

        await update.message.reply_text(
            "📊 حالة الذهب الآن\n\n"
            f"💰 السعر: {live_price:.2f}\n"
            f"📈 أعلى الشمعة: {last['high']:.2f}\n"
            f"📉 أدنى الشمعة: {last['low']:.2f}\n"
            f"📊 RSI: {last['rsi']:.1f}\n"
            f"📈 الاتجاه 1H: {trend}\n"
            f"📉 التغير: {change_text}\n"
            f"📡 المصدر: Yahoo Finance (GC=F)"
        )
    except Exception as exc:
        logger.exception("Status command failed")
        await update.message.reply_text(f"❌ تعذر جلب بيانات الذهب.\nالسبب التقني: {exc}")


# Keep the last automatic alert from repeating on every run.
last_auto_signal_key = None


async def auto_signals(context: ContextTypes.DEFAULT_TYPE):
    global last_auto_signal_key
    try:
        h1, h4 = get_gold_data()
        live_price, _ = get_live_quote()
        result = analyze_signal(h1, h4, live_price)
        if not result or not CHAT_ID:
            return

        signal_key = f"{result['direction']}|{result['candle_time']}"
        if signal_key == last_auto_signal_key:
            return

        last_auto_signal_key = signal_key
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=build_signal_message(result, automatic=True),
        )
    except Exception:
        logger.exception("Automatic signal failed")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 طريقة العمل:\n\n"
        "• بيانات الذهب من Yahoo Finance عبر رمز GC=F.\n"
        "• التحليل يستخدم 1H + 4H.\n"
        "• EMA20/50/200 + RSI + MACD + ATR + ADX.\n"
        "• لا يتم إرسال BUY/SELL إذا لم تتفق الشروط.\n"
        "• يتم تجاهل الشمعة غير المكتملة لتقليل الإشارات المتغيرة.\n\n"
        "⚠️ لا توجد خوارزمية تضمن الربح. استخدم إدارة رأس المال وراجع سعر وسيطك قبل الدخول."
    )


async def auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.get_jobs_by_name("gold_auto_signal")
    if jobs:
        await update.message.reply_text("ℹ️ الإشارات التلقائية مفعلة بالفعل.")
        return

    context.job_queue.run_repeating(
        auto_signals,
        interval=3600,
        first=5,
        name="gold_auto_signal",
    )
    await update.message.reply_text("✅ تم تفعيل الإشارات التلقائية كل ساعة.")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.get_jobs_by_name("gold_auto_signal")
    for job in jobs:
        job.schedule_removal()
    await update.message.reply_text("🛑 تم إيقاف الإشارات التلقائية.")


def validate_environment():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")
    if missing:
        raise RuntimeError("Missing Render environment variables: " + ", ".join(missing))


def main():
    validate_environment()
    keep_alive()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("signal", signal_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("auto", auto_command))
    application.add_handler(CommandHandler("stop", stop_command))

    # Check once shortly after startup. It will only send if a new strong signal exists.
    application.job_queue.run_once(auto_signals, 5, name="gold_startup_signal")

    print("🤖 Gold Signal Bot started successfully")
    print("📡 Data source: Yahoo Finance / GC=F (free)")
    application.run_polling()


if __name__ == "__main__":
    main()
