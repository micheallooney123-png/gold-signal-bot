"""قراءة إعدادات البوت من متغيرات البيئة (.env)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # مزود بيانات السعر (Twelve Data)
    price_api_key: str
    symbol: str  # مثال: "XAU/USD"

    # Trading
    timeframe: str  # مثال: M15, M30, H1
    poll_seconds: int  # كل كم ثانية يفحص البوت السوق

    # Risk / strategy
    ema_fast: int
    ema_slow: int
    rsi_period: int
    rsi_overbought: float
    rsi_oversold: float
    macd_fast: int
    macd_slow: int
    macd_signal: int
    atr_period: int
    sl_atr_multiplier: float
    tp_atr_multiplier: float
    swing_lookback: int


def load_settings() -> Settings:
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        price_api_key=os.getenv("PRICE_API_KEY", ""),
        symbol=os.getenv("SYMBOL", "XAU/USD"),
        timeframe=os.getenv("TIMEFRAME", "M15"),
        poll_seconds=_get_int("POLL_SECONDS", 60),
        ema_fast=_get_int("EMA_FAST", 12),
        ema_slow=_get_int("EMA_SLOW", 26),
        rsi_period=_get_int("RSI_PERIOD", 14),
        rsi_overbought=_get_float("RSI_OVERBOUGHT", 70.0),
        rsi_oversold=_get_float("RSI_OVERSOLD", 30.0),
        macd_fast=_get_int("MACD_FAST", 12),
        macd_slow=_get_int("MACD_SLOW", 26),
        macd_signal=_get_int("MACD_SIGNAL", 9),
        atr_period=_get_int("ATR_PERIOD", 14),
        sl_atr_multiplier=_get_float("SL_ATR_MULTIPLIER", 1.5),
        tp_atr_multiplier=_get_float("TP_ATR_MULTIPLIER", 2.0),
        swing_lookback=_get_int("SWING_LOOKBACK", 20),
    )


def validate_settings(settings: Settings) -> list:
    """يرجع قائمة بالأخطاء إذا كانت الإعدادات الأساسية ناقصة."""
    errors = []
    if not settings.telegram_bot_token:
        errors.append("TELEGRAM_BOT_TOKEN غير مضبوط")
    if not settings.telegram_chat_id:
        errors.append("TELEGRAM_CHAT_ID غير مضبوط")
    if not settings.price_api_key:
        errors.append("PRICE_API_KEY غير مضبوط")
    return errors
