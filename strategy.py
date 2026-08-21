"""
منطق توليد إشارات الشراء/البيع للذهب.

الإشارة تُولَّد فقط عند حدوث تقاطع "طازج" بين EMA السريع والبطيء
(لا إشارة إذا كان التقاطع قديمًا)، مع تأكيد من MACD، وفلترة RSI
لتفادي الدخول وقت التشبع الشرائي/البيعي.

هذه إشارات تحليل تقني فقط ولا تضمن الربح.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from config import Settings
from indicators import add_all_indicators, swing_levels

DISCLAIMER = (
    "⚠️ تنبيه: هذه توصية مبنية على تحليل تقني آلي فقط، وليست ضمانًا للربح. "
    "التداول ينطوي على مخاطر قد تصل لخسارة رأس المال. القرار والمسؤولية على المتداول."
)


@dataclass(frozen=True)
class Signal:
    direction: str  # "BUY" أو "SELL"
    symbol: str
    timeframe: str
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    candle_time: pd.Timestamp
    generated_at: datetime

    def signature(self) -> str:
        """معرّف يستخدم لتفادي إرسال نفس الإشارة مرتين لنفس الشمعة."""
        return f"{self.symbol}-{self.timeframe}-{self.candle_time}-{self.direction}"

    def to_message(self) -> str:
        emoji = "🟢" if self.direction == "BUY" else "🔴"
        direction_ar = "شراء" if self.direction == "BUY" else "بيع"
        return (
            f"{emoji} إشارة {direction_ar} - {self.symbol} ({self.timeframe})\n\n"
            f"سعر الدخول: {self.entry_price:.2f}\n"
            f"وقف الخسارة (SL): {self.stop_loss:.2f}\n"
            f"الهدف (TP): {self.take_profit:.2f}\n\n"
            f"السبب: {self.reason}\n\n"
            f"{DISCLAIMER}"
        )


def _fresh_cross(df: pd.DataFrame) -> Optional[str]:
    """يفحص آخر شمعتين لمعرفة إذا حصل تقاطع EMA حديث. يرجع 'up' أو 'down' أو None."""
    if len(df) < 2:
        return None
    prev, last = df.iloc[-2], df.iloc[-1]

    was_below = prev["ema_fast"] <= prev["ema_slow"]
    now_above = last["ema_fast"] > last["ema_slow"]
    if was_below and now_above:
        return "up"

    was_above = prev["ema_fast"] >= prev["ema_slow"]
    now_below = last["ema_fast"] < last["ema_slow"]
    if was_above and now_below:
        return "down"

    return None


def generate_signal(
    raw_df: pd.DataFrame, settings: Settings, symbol: str
) -> Optional[Signal]:
    """
    يحلل بيانات الشموع الخام (بدون مؤشرات) ويرجع Signal إذا توفرت شروط
    الدخول، أو None إذا لا توجد فرصة حاليًا.
    """
    df = add_all_indicators(
        raw_df,
        ema_fast=settings.ema_fast,
        ema_slow=settings.ema_slow,
        rsi_period=settings.rsi_period,
        macd_fast=settings.macd_fast,
        macd_slow=settings.macd_slow,
        macd_signal=settings.macd_signal,
        atr_period=settings.atr_period,
    )

    cross = _fresh_cross(df)
    if cross is None:
        return None

    last = df.iloc[-1]
    support, resistance = swing_levels(df, settings.swing_lookback)
    entry_price = float(last["close"])
    atr_value = float(last["atr"])

    if cross == "up":
        if last["macd_hist"] <= 0:
            return None
        if last["rsi"] >= settings.rsi_overbought:
            return None
        stop_loss = entry_price - atr_value * settings.sl_atr_multiplier
        take_profit = entry_price + atr_value * settings.tp_atr_multiplier
        reason = (
            f"تقاطع صعودي لـ EMA({settings.ema_fast}/{settings.ema_slow}) "
            f"مؤكد بـ MACD إيجابي، RSI={last['rsi']:.1f} (غير متشبع شرائيًا). "
            f"أقرب دعم: {support:.2f}، أقرب مقاومة: {resistance:.2f}."
        )
        direction = "BUY"
    else:
        if last["macd_hist"] >= 0:
            return None
        if last["rsi"] <= settings.rsi_oversold:
            return None
        stop_loss = entry_price + atr_value * settings.sl_atr_multiplier
        take_profit = entry_price - atr_value * settings.tp_atr_multiplier
        reason = (
            f"تقاطع نزولي لـ EMA({settings.ema_fast}/{settings.ema_slow}) "
            f"مؤكد بـ MACD سلبي، RSI={last['rsi']:.1f} (غير متشبع بيعيًا). "
            f"أقرب دعم: {support:.2f}، أقرب مقاومة: {resistance:.2f}."
        )
        direction = "SELL"

    return Signal(
        direction=direction,
        symbol=symbol,
        timeframe=settings.timeframe,
        entry_price=entry_price,
        stop_loss=round(stop_loss, 2),
        take_profit=round(take_profit, 2),
        reason=reason,
        candle_time=last["time"],
        generated_at=datetime.now(),
    )
