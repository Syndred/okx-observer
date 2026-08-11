from __future__ import annotations

import unittest

import pandas as pd

from user_data.strategy_lib.dual_ma_signal_engine import DualMaParameters, scan_dual_ma_setups


PAIR = "ALT/USDT:USDT"


def _ohlc(dates: pd.DatetimeIndex, close: float, widen: float = 0.2) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + widen,
            "low": close - widen,
            "close": close,
            "volume": 1000.0,
        }
    )


def long_fixture(**overrides) -> dict:
    start = pd.Timestamp("2025-01-01", tz="UTC")
    # Enough 1H history for slow EMA60.
    hours = pd.date_range(start, periods=80, freq="h", tz="UTC")
    closes = [90.0] * 40 + [95.0 + i * 0.4 for i in range(40)]
    candles1h = pd.DataFrame(
        {
            "date": hours,
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": 1000.0,
        }
    )
    # Last completed 1H should be available to 15m after +1h.
    signal_hour = hours[-1]
    quarters = pd.date_range(signal_hour + pd.Timedelta(hours=1), periods=8, freq="15min", tz="UTC")
    candles15m = _ohlc(quarters, close=112.0, widen=0.4)
    # First tradable pullback candle after armed signal candle.
    # 1H fast EMA ends near ~106.8 in this fixture, so the pullback must reach it.
    candles15m.loc[1, ["low", "close", "high"]] = [106.5, 107.2, 112.0]
    candles4h = candles1h.iloc[::4].copy().reset_index(drop=True)
    btc = candles4h.copy()
    eth = candles4h.copy()
    # Force own/market long regimes used by shared 4H merge helpers.
    for frame in (candles4h, btc, eth):
        frame["close"] = frame["close"].astype(float) + 20
    universe = pd.DataFrame(
        {
            "date": pd.date_range(quarters[0].floor("h"), periods=3, freq="h", tz="UTC"),
            "pair": PAIR,
            "eligible": True,
            "rank": 1,
        }
    )
    payload = dict(
        pair=PAIR,
        inst_category="3",
        candles15m=candles15m,
        candles1h=candles1h,
        candles4h=candles4h,
        btc4h=btc,
        eth4h=eth,
        universe_mask=universe,
        params=DualMaParameters(require_4h_trend=False, pullback_wait_15m=6),
    )
    payload.update(overrides)
    return payload


class DualMaSignalTests(unittest.TestCase):
    def test_first_pullback_to_fast_ma_enters_long(self) -> None:
        result = scan_dual_ma_setups(**long_fixture())
        entries = list(result.index[result["enter_long"] == 1])
        self.assertEqual(entries, [1])
        self.assertLess(result.loc[1, "initial_stop_price"], result.loc[1, "close"])

    def test_arm_candle_cannot_also_be_pullback(self) -> None:
        fixture = long_fixture()
        fixture["candles15m"].loc[0, ["low", "close", "high"]] = [106.5, 107.2, 112.0]
        fixture["candles15m"].loc[1:, "low"] = 112.5
        result = scan_dual_ma_setups(**fixture)
        self.assertEqual(result["enter_long"].sum(), 0)

    def test_outside_universe_cannot_enter(self) -> None:
        fixture = long_fixture()
        fixture["universe_mask"]["pair"] = "OTHER/USDT:USDT"
        result = scan_dual_ma_setups(**fixture)
        self.assertEqual(int(result["enter_long"].sum()), 0)

    def test_crypto_still_respects_market_filter(self) -> None:
        fixture = long_fixture(inst_category="1")
        # Leave BTC/ETH without a supportive long regime.
        for key in ("btc4h", "eth4h"):
            frame = fixture[key].copy()
            frame["close"] = 50.0
            frame["open"] = 50.0
            frame["high"] = 50.5
            frame["low"] = 49.5
            fixture[key] = frame
        result = scan_dual_ma_setups(**fixture)
        self.assertEqual(int(result["enter_long"].sum()), 0)

    def test_short_is_symmetric(self) -> None:
        fixture = long_fixture()
        # Invert prices into a falling dual-MA environment.
        for key in ("candles1h", "candles4h", "btc4h", "eth4h"):
            frame = fixture[key].copy()
            frame["close"] = 200 - frame["close"]
            frame["open"] = frame["close"]
            frame["high"] = frame["close"] + 0.5
            frame["low"] = frame["close"] - 0.5
            fixture[key] = frame
        quarters = fixture["candles15m"]["date"]
        fixture["candles15m"] = _ohlc(quarters, close=88.0, widen=0.4)
        # Mirror of the long fixture: bounce up into the fast EMA band, close still below it.
        fixture["candles15m"].loc[1, ["high", "close", "low"]] = [93.5, 92.8, 88.0]
        result = scan_dual_ma_setups(**fixture)
        self.assertEqual(list(result.index[result["enter_short"] == 1]), [1])
        self.assertGreater(result.loc[1, "initial_stop_price"], result.loc[1, "close"])


if __name__ == "__main__":
    unittest.main()
