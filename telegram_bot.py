"""بوت تليغرام لإرسال تنبيهات إشارات الذهب، وأوامر استعلام بسيطة."""
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import Settings
from price_feed import PriceFeed, PriceFeedError
from strategy import generate_signal

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "أوامر البوت:\n"
    "/status - حالة الاتصال بمزود بيانات السعر وآخر إشارة تم إرسالها\n"
    "/price - السعر الحالي للذهب\n"
    "/help - عرض هذه الرسالة\n\n"
    "البوت يرسل تنبيهًا تلقائيًا فقط عند ظهور فرصة جديدة حسب المؤشرات التقنية. "
    "هذه تنبيهات وليست تنفيذًا تلقائيًا للصفقات، والقرار النهائي دائمًا لك."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "أهلًا! أنا بوت تنبيهات صفقات الذهب (XAU/USD).\n\n" + HELP_TEXT
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    feed: PriceFeed = context.bot_data["feed"]
    last_signature: str = context.bot_data.get("last_signature") or "لا توجد إشارة بعد"

    if feed.is_connected():
        status_line = f"متصل بمزود بيانات السعر. الرمز: {feed.resolved_symbol}"
    else:
        status_line = "غير متصل بمزود بيانات السعر حاليًا."

    await update.message.reply_text(
        f"{status_line}\n\nآخر إشارة مُرسلة: {last_signature}"
    )


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    feed: PriceFeed = context.bot_data["feed"]
    try:
        price = feed.get_current_price()
        await update.message.reply_text(f"السعر الحالي لـ {feed.resolved_symbol}: {price:.2f}")
    except PriceFeedError as exc:
        await update.message.reply_text(f"تعذر جلب السعر الحالي: {exc}")


async def job_check_market(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    feed: PriceFeed = context.bot_data["feed"]

    try:
        candles = feed.get_candles(count=200)
        signal = generate_signal(candles, settings, feed.resolved_symbol)
    except PriceFeedError as exc:
        logger.error("فشل تحليل السوق: %s", exc)
        return

    if signal is None:
        return

    if context.bot_data.get("last_signature") == signal.signature():
        return  # نفس الإشارة تم إرسالها لهذه الشمعة، لا تكرار

    context.bot_data["last_signature"] = signal.signature()
    await context.bot.send_message(
        chat_id=settings.telegram_chat_id, text=signal.to_message()
    )
    logger.info("تم إرسال إشارة: %s", signal.signature())


def build_application(settings: Settings, feed: PriceFeed) -> Application:
    application = Application.builder().token(settings.telegram_bot_token).build()

    application.bot_data["settings"] = settings
    application.bot_data["feed"] = feed
    application.bot_data["last_signature"] = None

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("price", cmd_price))

    application.job_queue.run_repeating(
        job_check_market, interval=settings.poll_seconds, first=5
    )

    return application
