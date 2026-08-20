import os
import logging
import threading
from datetime import datetime, timezone

import requests
import pandas as pd
from flask import Flask

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# إعدادات البوت
# ============================================================

TOKEN = 8600613901:AAG3Kvdlgy1yM0pU_GgAIis2Qb-B9g-GwTo
CHAT_ID = 6532633465

SYMBOL = "XAUUSD=X"
INTERVAL = "1h"
DATA_RANGE = "10d"

AUTO_INTERVAL = 3600          # كل ساعة
AUTO_FIRST_RUN = 20            # أول فحص بعد 20 ثانية

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN غير موجود. أضفه في Render → Environment."
    )

if not CHAT_ID:
    logging.warning(
        "CHAT_ID غير موجود. الأوامر ستعمل، لكن التنبيهات التلقائية لن تُرسل."
    )


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger("GoldSignalBot")


# ============================================================
# Flask - حتى تبقى خدمة Render تعمل
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "✅ Gold Signal Bot is Running!"


def run_flask():
    port = int(os.getenv("PORT", "8080"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


def keep_alive():
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()


# ============================================================
# جلب بيانات الذهب
# ============================================================

def get_gold_data():
    """
    جلب بيانات XAU/USD من Yahoo Finance.
    نستخدم شموع الساعة لآخر 10 أيام.
    """

    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X"

        params = {
            "interval": INTERVAL,
            "range": DATA_RANGE,
            "events": "history"
        }

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        chart = data.get("chart", {})

        if chart.get("error"):
            raise RuntimeError(str(chart["error"]))

        results = chart.get("result")

        if not results:
            raise RuntimeError("Yahoo Finance لم يرجع بيانات.")

        result = results[0]

        timestamps = result.get("timestamp")
        indicators = result.get("indicators", {})
        quotes = indicators.get("quote", [])

        if not timestamps or not quotes:
            raise RuntimeError("بيانات الأسعار فارغة.")

        prices = quotes[0]

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(
                timestamps,
                unit="s",
                utc=True
            ),
            "open": prices.get("open"),
            "high": prices.get("high"),
            "low": prices.get("low"),
            "close": prices.get("close"),
            "volume": prices.get("volume")
        })

        df = df.dropna(
            subset=["open", "high", "low", "close"]
        ).copy()

        if len(df) < 220:
            raise RuntimeError(
                f"عدد الشموع غير كافٍ للتحليل: {len(df)}"
            )

        # لا نعتمد على الشمعة الحالية غير المكتملة.
        df = df.iloc[:-1].copy()

        return df

    except Exception as e:
        logger.exception("Error fetching gold data: %s", e)
        return None


# ============================================================
# المؤشرات الفنية
# ============================================================

def calculate_indicators(df):
    df = df.copy()

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    df["ema20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    df["ema50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    df["ema200"] = close.ewm(
        span=200,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # RSI 14
    # --------------------------------------------------------

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df["macd"].ewm(
        span=9,
        adjust=False
    ).mean()

    df["macd_hist"] = (
        df["macd"] - df["macd_signal"]
    )

    # --------------------------------------------------------
    # ATR 14
    # --------------------------------------------------------

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["atr"] = true_range.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    df["momentum"] = close.pct_change(5) * 100

    return df


# ============================================================
# تحليل الإشارة
# ============================================================

def analyze_signal(df):

    if df is None or len(df) < 220:
        return None

    df = calculate_indicators(df)

    last = df.iloc[-1]
    previous = df.iloc[-2]

    values = [
        last["close"],
        last["ema20"],
        last["ema50"],
        last["ema200"],
        last["rsi"],
        last["macd"],
        last["macd_signal"],
        last["atr"]
    ]

    if any(pd.isna(value) for value in values):
        return None

    price = float(last["close"])
    ema20 = float(last["ema20"])
    ema50 = float(last["ema50"])
    ema200 = float(last["ema200"])
    rsi = float(last["rsi"])
    macd = float(last["macd"])
    macd_signal = float(last["macd_signal"])
    macd_hist = float(last["macd_hist"])
    atr = float(last["atr"])
    momentum = float(last["momentum"])

    if atr <= 0:
        return None

    # ========================================================
    # BUY SCORE
    # ========================================================

    buy_score = 0
    buy_reasons = []

    # الاتجاه الرئيسي
    if ema20 > ema50 > ema200:
        buy_score += 2
        buy_reasons.append("الاتجاه الرئيسي صاعد")

    # السعر فوق EMA20
    if price > ema20:
        buy_score += 1
        buy_reasons.append("السعر فوق EMA20")

    # RSI
    if 52 <= rsi <= 68:
        buy_score += 1
        buy_reasons.append("RSI يدعم الشراء")

    # MACD
    if macd > macd_signal and macd_hist > 0:
        buy_score += 1
        buy_reasons.append("MACD إيجابي")

    # Momentum
    if momentum > 0:
        buy_score += 1
        buy_reasons.append("الزخم إيجابي")

    # تقاطع EMA20 حديث
    if (
        previous["close"] <= previous["ema20"]
        and price > ema20
    ):
        buy_score += 1
        buy_reasons.append("اختراق EMA20")

    # ========================================================
    # SELL SCORE
    # ========================================================

    sell_score = 0
    sell_reasons = []

    # الاتجاه الرئيسي
    if ema20 < ema50 < ema200:
        sell_score += 2
        sell_reasons.append("الاتجاه الرئيسي هابط")

    # السعر تحت EMA20
    if price < ema20:
        sell_score += 1
        sell_reasons.append("السعر تحت EMA20")

    # RSI
    if 32 <= rsi <= 48:
        sell_score += 1
        sell_reasons.append("RSI يدعم البيع")

    # MACD
    if macd < macd_signal and macd_hist < 0:
        sell_score += 1
        sell_reasons.append("MACD سلبي")

    # Momentum
    if momentum < 0:
        sell_score += 1
        sell_reasons.append("الزخم سلبي")

    # كسر EMA20 حديث
    if (
        previous["close"] >= previous["ema20"]
        and price < ema20
    ):
        sell_score += 1
        sell_reasons.append("كسر EMA20")

    # ========================================================
    # اختيار الإشارة
    # ========================================================

    minimum_score = 5

    if buy_score >= minimum_score and buy_score > sell_score:

        entry = price

        sl = entry - (atr * 1.5)
        tp1 = entry + (atr * 2.0)
        tp2 = entry + (atr * 3.0)

        return {
            "direction": "BUY",
            "signal": "🟢 شراء BUY",
            "price": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "rsi": rsi,
            "macd": macd,
            "atr": atr,
            "score": buy_score,
            "reason": " | ".join(buy_reasons),
            "timestamp": last["timestamp"]
        }

    if sell_score >= minimum_score and sell_score > buy_score:

        entry = price

        sl = entry + (atr * 1.5)
        tp1 = entry - (atr * 2.0)
        tp2 = entry - (atr * 3.0)

        return {
            "direction": "SELL",
            "signal": "🔴 بيع SELL",
            "price": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "rsi": rsi,
            "macd": macd,
            "atr": atr,
            "score": sell_score,
            "reason": " | ".join(sell_reasons),
            "timestamp": last["timestamp"]
        }

    return None


# ============================================================
# تنسيق الإشارة
# ============================================================

def format_signal(result, automatic=False):

    title = (
        "🔔 إشارة تلقائية للذهب"
        if automatic
        else "⚡ تحليل الذهب XAU/USD"
    )

    direction = result["signal"]

    return (
        f"{title}\n\n"
        f"📌 {direction}\n\n"
        f"💰 Entry: {result['price']:.2f}\n"
        f"🛑 Stop Loss: {result['sl']:.2f}\n"
        f"🎯 TP1: {result['tp1']:.2f}\n"
        f"🎯 TP2: {result['tp2']:.2f}\n\n"
        f"📊 قوة الإشارة: {result['score']}/7\n"
        f"📈 RSI: {result['rsi']:.1f}\n"
        f"📉 MACD: {result['macd']:.4f}\n"
        f"📏 ATR: {result['atr']:.2f}\n\n"
        f"🔎 الأسباب:\n"
        f"{result['reason']}\n\n"
        f"🕐 الشمعة: {result['timestamp']}\n\n"
        f"⚠️ إشارة تحليلية وليست ضمانًا للربح.\n"
        f"استخدم إدارة مخاطر ووقف الخسارة دائمًا."
    )


# ============================================================
# /start
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🤖 بوت إشارات الذهب XAU/USD\n\n"
        "📊 الأوامر:\n"
        "/signal - تحليل الذهب الآن\n"
        "/status - حالة السوق\n"
        "/auto - تشغيل الإشارات التلقائية\n"
        "/stop - إيقاف الإشارات التلقائية\n"
        "/help - المساعدة\n\n"
        "⏰ الإشارات التلقائية تعمل كل ساعة."
    )


# ============================================================
# /help
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📖 دليل البوت\n\n"
        "/signal\n"
        "تحليل XAU/USD وإظهار BUY أو SELL إذا كانت الشروط قوية.\n\n"
        "/status\n"
        "يعرض السعر والمؤشرات الحالية.\n\n"
        "/auto\n"
        "تشغيل التنبيهات التلقائية كل ساعة.\n\n"
        "/stop\n"
        "إيقاف التنبيهات التلقائية فقط.\n\n"
        "📊 المؤشرات المستخدمة:\n"
        "• EMA 20\n"
        "• EMA 50\n"
        "• EMA 200\n"
        "• RSI 14\n"
        "• MACD\n"
        "• ATR 14\n\n"
        "⚠️ لا توجد استراتيجية تضمن الربح."
    )


# ============================================================
# /signal
# ============================================================

async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "⏳ جاري تحليل XAU/USD...\n"
        "📊 أفحص الاتجاه + RSI + MACD + ATR..."
    )

    df = get_gold_data()
    result = analyze_signal(df)

    if result:

        message = format_signal(result)

    else:

        message = (
            "⏸️ لا توجد صفقة قوية حاليًا.\n\n"
            "📊 شروط BUY/SELL لم تصل إلى المستوى المطلوب.\n"
            "الأفضل انتظار فرصة أوضح بدل الدخول عشوائيًا.\n\n"
            "💡 أعد المحاولة مع الشمعة القادمة."
        )

    await update.message.reply_text(message)


# ============================================================
# /status
# ============================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "⏳ جاري جلب بيانات الذهب..."
    )

    df = get_gold_data()

    if df is None:

        await update.message.reply_text(
            "❌ تعذر جلب بيانات XAU/USD حاليًا.\n"
            "حاول مرة أخرى بعد قليل."
        )

        return

    df = calculate_indicators(df)

    last = df.iloc[-1]

    price = float(last["close"])
    rsi = float(last["rsi"])
    ema20 = float(last["ema20"])
    ema50 = float(last["ema50"])
    ema200 = float(last["ema200"])
    macd = float(last["macd"])
    macd_signal = float(last["macd_signal"])
    atr = float(last["atr"])

    if ema20 > ema50 > ema200:
        trend = "🟢 صاعد"

    elif ema20 < ema50 < ema200:
        trend = "🔴 هابط"

    else:
        trend = "🟡 متذبذب / غير واضح"

    await update.message.reply_text(
        f"📊 حالة الذهب XAU/USD\n\n"
        f"💰 السعر: {price:.2f}\n\n"
        f"📈 الاتجاه: {trend}\n"
        f"EMA20: {ema20:.2f}\n"
        f"EMA50: {ema50:.2f}\n"
        f"EMA200: {ema200:.2f}\n\n"
        f"RSI: {rsi:.1f}\n"
        f"MACD: {macd:.4f}\n"
        f"MACD Signal: {macd_signal:.4f}\n"
        f"ATR: {atr:.2f}\n\n"
        f"🕐 آخر شمعة مكتملة:\n"
        f"{last['timestamp']}"
    )


# ============================================================
# منع تكرار نفس الإشارة
# ============================================================

last_sent_signal = None


# ============================================================
# الإشارات التلقائية
# ============================================================

async def auto_signals(
    context: ContextTypes.DEFAULT_TYPE
):

    global last_sent_signal

    if not CHAT_ID:
        logger.warning(
            "CHAT_ID غير موجود، تم تخطي الإشارة التلقائية."
        )
        return

    try:

        df = get_gold_data()
        result = analyze_signal(df)

        if not result:
            logger.info(
                "لا توجد إشارة قوية في الفحص التلقائي."
            )
            return

        candle_time = str(result["timestamp"])

        signal_key = (
            f"{result['direction']}_{candle_time}"
        )

        # منع إرسال نفس الإشارة أكثر من مرة
        if signal_key == last_sent_signal:
            logger.info(
                "تم تجاهل إشارة مكررة: %s",
                signal_key
            )
            return

        message = format_signal(
            result,
            automatic=True
        )

        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=message
        )

        last_sent_signal = signal_key

        logger.info(
            "تم إرسال إشارة %s",
            signal_key
        )

    except Exception as e:

        logger.exception(
            "Auto signal error: %s",
            e
        )


# ============================================================
# /auto
# ============================================================

async def auto_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    job_name = "gold_auto_signals"

    existing_jobs = context.job_queue.get_jobs_by_name(
        job_name
    )

    if existing_jobs:

        await update.message.reply_text(
            "✅ الإشارات التلقائية تعمل بالفعل.\n"
            "⏰ يتم الفحص كل ساعة."
        )

        return

    context.job_queue.run_repeating(
        auto_signals,
        interval=AUTO_INTERVAL,
        first=AUTO_FIRST_RUN,
        name=job_name
    )

    await update.message.reply_text(
        "✅ تم تشغيل الإشارات التلقائية.\n\n"
        "⏰ سيتم فحص الذهب كل ساعة.\n"
        "📊 لن يتم إرسال تنبيه إلا إذا كانت الإشارة قوية."
    )


# ============================================================
# /stop
# ============================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    job_name = "gold_auto_signals"

    jobs = context.job_queue.get_jobs_by_name(
        job_name
    )

    if not jobs:

        await update.message.reply_text(
            "ℹ️ الإشارات التلقائية متوقفة بالفعل."
        )

        return

    for job in jobs:
        job.schedule_removal()

    await update.message.reply_text(
        "🛑 تم إيقاف الإشارات التلقائية.\n"
        "البوت نفسه ما زال يعمل."
    )


# ============================================================
# Error Handler
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram error: %s",
        context.error
    )


# ============================================================
# Main
# ============================================================

def main():

    keep_alive()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    # الأوامر
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("signal", signal_command)
    )

    application.add_handler(
        CommandHandler("status", status_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("auto", auto_command)
    )

    application.add_handler(
        CommandHandler("stop", stop_command)
    )

    application.add_error_handler(
        error_handler
    )

    # تشغيل التنبيهات تلقائيًا عند تشغيل Render
    application.job_queue.run_repeating(
        auto_signals,
        interval=AUTO_INTERVAL,
        first=AUTO_FIRST_RUN,
        name="gold_auto_signals"
    )

    logger.info(
        "🤖 Gold Signal Bot started successfully."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
    
    print("🤖 البوت يعمل الآن...")
    application.run_polling()

if __name__ == "__main__":
    main()
