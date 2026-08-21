import numpy as np
import pandas as pd

from indicators import add_all_indicators, atr, ema, macd, rsi, swing_levels


def _make_ohlc(closes):
    closes = pd.Series(closes, dtype=float)
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=len(closes), freq="15min"),
            "open": closes,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
        }
    )
    return df


def test_ema_matches_pandas_ewm():
    series = pd.Series(np.linspace(1900, 2000, 30))
    result = ema(series, 10)
    expected = series.ewm(span=10, adjust=False).mean()
    pd.testing.assert_series_equal(result, expected)


def test_rsi_is_high_for_strictly_rising_series():
    series = pd.Series(np.linspace(1900, 2000, 30))
    result = rsi(series, 14)
    # سلسلة صاعدة باستمرار => RSI يجب أن يقترب من 100 في النهاية
    assert result.iloc[-1] > 90


def test_rsi_is_low_for_strictly_falling_series():
    series = pd.Series(np.linspace(2000, 1900, 30))
    result = rsi(series, 14)
    assert result.iloc[-1] < 10


def test_macd_histogram_positive_when_trending_up():
    series = pd.Series(np.linspace(1900, 2000, 40))
    result = macd(series, fast=12, slow=26, signal=9)
    assert result["histogram"].iloc[-1] >= 0


def test_atr_is_positive_and_reflects_volatility():
    closes = np.concatenate([np.full(10, 1900.0), np.linspace(1900, 1950, 10)])
    df = _make_ohlc(closes)
    result = atr(df, period=14)
    assert (result.dropna() > 0).all()


def test_swing_levels_returns_support_below_resistance():
    closes = np.linspace(1900, 1950, 25)
    df = _make_ohlc(closes)
    support, resistance = swing_levels(df, lookback=20)
    assert support < resistance


def test_add_all_indicators_adds_expected_columns():
    closes = np.linspace(1900, 1950, 40)
    df = _make_ohlc(closes)
    out = add_all_indicators(df)
    for col in ["ema_fast", "ema_slow", "rsi", "macd", "macd_signal", "macd_hist", "atr"]:
        assert col in out.columns
