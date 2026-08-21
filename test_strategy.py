import numpy as np
import pandas as pd

from config import Settings
from strategy import generate_signal


def _make_settings() -> Settings:
    return Settings(
        telegram_bot_token="dummy",
        telegram_chat_id="dummy",
        price_api_key="dummy",
        symbol="XAU/USD",
        timeframe="M15",
        poll_seconds=60,
        ema_fast=3,
        ema_slow=6,
        rsi_period=5,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        macd_fast=3,
        macd_slow=6,
        macd_signal=3,
        atr_period=5,
        sl_atr_multiplier=1.5,
        tp_atr_multiplier=2.0,
        swing_lookback=10,
    )


def _make_ohlc(closes) -> pd.DataFrame:
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=len(closes), freq="15min"),
            "open": closes,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
        }
    )


def test_generate_signal_returns_buy_on_fresh_bullish_cross():
    declining = np.linspace(1950, 1900, 20)
    rising = np.linspace(1900, 1920, 3)[1:]  # [1910, 1920]
    closes = np.concatenate([declining, rising])
    df = _make_ohlc(closes)

    signal = generate_signal(df, _make_settings(), symbol="XAUUSD")

    assert signal is not None
    assert signal.direction == "BUY"
    assert signal.stop_loss < signal.entry_price < signal.take_profit
    assert "XAUUSD" in signal.to_message()


def test_generate_signal_returns_sell_on_fresh_bearish_cross():
    rising = np.linspace(1900, 1950, 20)
    falling = np.linspace(1950, 1930, 3)[1:]  # [1940, 1930]
    closes = np.concatenate([rising, falling])
    df = _make_ohlc(closes)

    signal = generate_signal(df, _make_settings(), symbol="XAUUSD")

    assert signal is not None
    assert signal.direction == "SELL"
    assert signal.take_profit < signal.entry_price < signal.stop_loss


def test_generate_signal_returns_none_when_no_fresh_cross():
    # سعر ثابت تمامًا => لا فرق بين EMA السريع والبطيء => لا تقاطع => لا إشارة
    closes = np.full(30, 1900.0)
    df = _make_ohlc(closes)

    signal = generate_signal(df, _make_settings(), symbol="XAUUSD")

    assert signal is None


def test_signal_signature_is_stable_for_same_candle():
    declining = np.linspace(1950, 1900, 20)
    rising = np.linspace(1900, 1920, 3)[1:]
    closes = np.concatenate([declining, rising])
    df = _make_ohlc(closes)
    settings = _make_settings()

    signal_a = generate_signal(df, settings, symbol="XAUUSD")
    signal_b = generate_signal(df, settings, symbol="XAUUSD")

    assert signal_a.signature() == signal_b.signature()
