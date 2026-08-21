"""نقطة تشغيل بوت تنبيهات صفقات الذهب."""
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import load_settings, validate_settings
from price_feed import PriceFeed, PriceFeedError
from telegram_bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (اسم الدالة مفروض من BaseHTTPRequestHandler)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format: str, *args) -> None:  # كتم سجلات HTTP الزائدة
        pass


def _start_health_server_if_needed() -> None:
    """
    بعض خدمات الاستضافة (مثل Render Web Service) تتطلب أن يستمع التطبيق
    على متغير البيئة PORT ويرد على HTTP، وإلا يعتبر النشر فاشلاً.
    إذا لم يكن PORT مضبوطًا (مثلاً عند التشغيل كـ Background Worker) يتم تجاوز هذا.
    """
    port = os.getenv("PORT")
    if not port:
        return

    server = HTTPServer(("0.0.0.0", int(port)), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("خادم فحص الصحة (health check) يعمل على المنفذ %s", port)


def main() -> None:
    settings = load_settings()
    errors = validate_settings(settings)
    if errors:
        for err in errors:
            logger.error(err)
        logger.error("راجع متغيرات البيئة (.env محليًا أو Environment على Render) وعبّئ القيم الناقصة.")
        sys.exit(1)

    _start_health_server_if_needed()

    feed = PriceFeed(settings)
    try:
        feed.connect()
    except PriceFeedError as exc:
        logger.error("فشل الاتصال بمزود بيانات السعر: %s", exc)
        logger.error("تحقق من صحة PRICE_API_KEY و SYMBOL في الإعدادات.")
        sys.exit(1)

    application = build_application(settings, feed)
    logger.info(
        "البوت يعمل الآن. الرمز: %s | الفريم: %s | كل %s ثانية.",
        feed.resolved_symbol,
        settings.timeframe,
        settings.poll_seconds,
    )

    try:
        application.run_polling()
    finally:
        feed.disconnect()


if __name__ == "__main__":
    main()
