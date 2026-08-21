"""
مصدر بيانات سعر الذهب عبر Twelve Data (خدمة سحابية عامة).

هذا المصدر لا يتصل بحساب XM مباشرة، بل يجلب سعر السوق الحقيقي لـ XAU/USD
من مزود بيانات مالية عام. السعر نفسه هو سعر السوق العالمي، وهو قريب جدًا
من السعر داخل XM (قد يوجد فرق بسيط بسبب السبريد/العمولة الخاصة بالوسيط)،
لكن هذا المصدر يعمل من أي سيرفر سحابي (مثل Render) بدون الحاجة لتشغيل
MetaTrader محليًا.

يمكن استبدال هذا الملف بمزود آخر (Alpha Vantage, Metals-API...) بنفس الواجهة
(get_candles / get_current_price) دون تعديل باقي المشروع.
"""
import logging

import pandas as pd
import requests

from config import Settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twelvedata.com"

INTERVAL_MAP = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1day",
}


class PriceFeedError(RuntimeError):
    pass


class PriceFeed:
    """يجلب شموع وسعر الذهب من Twelve Data باستخدام رمز XAU/USD."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.resolved_symbol = settings.symbol
        self._validated = False

    def connect(self) -> None:
        """فحص سريع يتأكد أن مفتاح API صالح وأن الرمز متاح، قبل بدء البوت."""
        self.get_candles(count=5)
        self._validated = True
        logger.info("تم التحقق من الاتصال بمزود بيانات السعر. الرمز: %s", self.resolved_symbol)

    def disconnect(self) -> None:
        self._validated = False

    def is_connected(self) -> bool:
        return self._validated

    def _interval(self) -> str:
        interval = INTERVAL_MAP.get(self.settings.timeframe)
        if interval is None:
            raise ValueError(f"فريم زمني غير مدعوم: {self.settings.timeframe}")
        return interval

    def get_candles(self, count: int = 200) -> pd.DataFrame:
        params = {
            "symbol": self.resolved_symbol,
            "interval": self._interval(),
            "outputsize": count,
            "apikey": self.settings.price_api_key,
        }
        try:
            response = requests.get(f"{BASE_URL}/time_series", params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PriceFeedError(f"فشل الاتصال بمزود بيانات السعر: {exc}") from exc

        if payload.get("status") == "error":
            raise PriceFeedError(f"خطأ من مزود بيانات السعر: {payload.get('message')}")

        values = payload.get("values")
        if not values:
            raise PriceFeedError(f"لم يتم إرجاع بيانات شموع لرمز {self.resolved_symbol}")

        df = pd.DataFrame(values)
        df = df.rename(columns={"datetime": "time"})
        df["time"] = pd.to_datetime(df["time"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)

        # Twelve Data يرجع الأحدث أولًا، نعيد الترتيب زمنيًا تصاعديًا
        df = df.sort_values("time").reset_index(drop=True)
        return df[["time", "open", "high", "low", "close"]]

    def get_current_price(self) -> float:
        params = {"symbol": self.resolved_symbol, "apikey": self.settings.price_api_key}
        try:
            response = requests.get(f"{BASE_URL}/price", params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PriceFeedError(f"فشل جلب السعر الحالي: {exc}") from exc

        if "price" not in payload:
            raise PriceFeedError(f"استجابة غير متوقعة من مزود بيانات السعر: {payload}")

        return float(payload["price"])
