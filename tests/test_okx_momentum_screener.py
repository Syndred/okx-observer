from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from user_data.strategy_lib.okx_momentum_screener import (
    MomentumParameters,
    close_to_close_streak,
    momentum_snapshot,
    screen_instruments,
    volume_ratio,
)


UTC = "UTC"
START = pd.Timestamp("2025-01-01", tz=UTC)


class OkxMomentumScreenerTests(unittest.TestCase):
    def test_close_to_close_streak_stops_on_doji_or_reversal(self) -> None:
        streak, direction = close_to_close_streak(np.array([10.0, 11.0, 12.0, 13.0]))
        self.assertEqual((streak, direction), (3, "long"))

        streak, direction = close_to_close_streak(np.array([13.0, 12.0, 11.0, 10.0]))
        self.assertEqual((streak, direction), (3, "short"))

        streak, direction = close_to_close_streak(np.array([10.0, 11.0, 11.0, 12.0]))
        self.assertEqual((streak, direction), (1, "long"))

        streak, direction = close_to_close_streak(np.array([10.0]))
        self.assertEqual((streak, direction), (0, "none"))

    def test_volume_ratio_excludes_the_current_bar(self) -> None:
        volumes = np.array([100.0] * 20 + [250.0])
        self.assertAlmostEqual(volume_ratio(volumes, 20), 2.5)

    def test_two_up_days_without_expansion_are_watch_only(self) -> None:
        daily = rising_days(up_days=2, last_volume_mult=1.0)
        snapshot = momentum_snapshot(daily, pd.DataFrame())
        self.assertEqual(snapshot["status"], "watch_up")
        self.assertEqual(snapshot["direction"], "long")
        self.assertEqual(int(snapshot["daily_streak"]), 2)
        self.assertFalse(bool(snapshot["candidate"]))

    def test_two_up_days_with_volume_expansion_are_momentum(self) -> None:
        daily = rising_days(up_days=2, last_volume_mult=2.4)
        snapshot = momentum_snapshot(daily, pd.DataFrame())
        self.assertEqual(snapshot["status"], "momentum_up")
        self.assertTrue(bool(snapshot["candidate"]))
        self.assertGreaterEqual(float(snapshot["volume_ratio"]), 2.0)

    def test_two_down_days_with_atr_expansion_are_momentum(self) -> None:
        daily = falling_days(down_days=2, last_range=6.0)
        snapshot = momentum_snapshot(daily, pd.DataFrame())
        self.assertEqual(snapshot["status"], "momentum_down")
        self.assertEqual(snapshot["direction"], "short")
        self.assertGreaterEqual(float(snapshot["atr_ratio"]), 1.5)
        self.assertTrue(bool(snapshot["candidate"]))

    def test_first_expanded_up_day_is_a_volume_spike(self) -> None:
        daily = rising_days(up_days=1, last_volume_mult=3.0)
        snapshot = momentum_snapshot(daily, pd.DataFrame())
        self.assertEqual(snapshot["status"], "volume_spike_up")
        self.assertTrue(bool(snapshot["candidate"]))

    def test_four_hour_streak_can_flag_an_intraday_launch(self) -> None:
        daily = rising_days(up_days=1, last_volume_mult=1.0)
        four_hour = rising_hours(up_bars=4, last_volume_mult=2.5)
        snapshot = momentum_snapshot(daily, four_hour)
        self.assertEqual(snapshot["status"], "four_hour_up")
        self.assertEqual(int(snapshot["four_hour_streak"]), 4)
        self.assertTrue(bool(snapshot["candidate"]))

    def test_ticker_only_move_is_intraday_watch_not_a_candidate(self) -> None:
        daily = rising_days(up_days=1, last_volume_mult=1.0)
        snapshot = momentum_snapshot(
            daily,
            pd.DataFrame(),
            ticker={"last": "108", "open24h": "100", "volCcy24h": "1"},
        )
        self.assertEqual(snapshot["status"], "intraday_up")
        self.assertAlmostEqual(float(snapshot["change_24h"]), 0.08)
        self.assertFalse(bool(snapshot["candidate"]))

    def test_sandisk_like_three_day_acceleration_is_momentum_up(self) -> None:
        daily = rising_days(up_days=3, last_volume_mult=2.2, step=0.08)
        snapshot = momentum_snapshot(daily, pd.DataFrame())
        self.assertEqual(snapshot["status"], "momentum_up")
        self.assertEqual(int(snapshot["daily_streak"]), 3)
        self.assertGreater(float(snapshot["score"]), 0)

    def test_insufficient_history_is_not_a_candidate(self) -> None:
        daily = rising_days(up_days=2, last_volume_mult=3.0, history=8)
        snapshot = momentum_snapshot(daily, pd.DataFrame())
        self.assertEqual(snapshot["status"], "insufficient")
        self.assertFalse(bool(snapshot["candidate"]))

    def test_parameters_must_be_positive_and_bounded(self) -> None:
        with self.assertRaises(ValueError):
            momentum_snapshot(rising_days(), pd.DataFrame(), params=MomentumParameters(min_daily_streak=0))

    def test_screen_excludes_references_and_young_listings(self) -> None:
        daily = rising_days(up_days=3, last_volume_mult=2.5)
        results = screen_instruments(
            [
                instrument("SNDK-USDT-SWAP"),
                instrument("BTC-USDT-SWAP", role="reference"),
                instrument(
                    "NEW-USDT-SWAP",
                    eligible=False,
                    age=3,
                    reason="listed_less_than_30_days",
                ),
            ],
            {
                "SNDK-USDT-SWAP": daily,
                "BTC-USDT-SWAP": daily,
                "NEW-USDT-SWAP": daily,
            },
            {},
            {},
        )
        by_id = results.set_index("instrument")
        self.assertTrue(bool(by_id.loc["SNDK-USDT-SWAP", "candidate"]))
        self.assertEqual(by_id.loc["SNDK-USDT-SWAP", "status"], "momentum_up")
        self.assertFalse(bool(by_id.loc["BTC-USDT-SWAP", "candidate"]))
        self.assertEqual(by_id.loc["BTC-USDT-SWAP", "status"], "reference_only")
        self.assertEqual(by_id.loc["NEW-USDT-SWAP", "status"], "listed_less_than_30_days")
        self.assertEqual(results.iloc[0]["instrument"], "SNDK-USDT-SWAP")


def instrument(
    inst_id: str,
    *,
    role: str = "trade",
    eligible: bool = True,
    age: int = 90,
    reason: str = "eligible",
    category: str = "crypto",
) -> dict[str, object]:
    return {
        "instId": inst_id,
        "base": inst_id.split("-")[0],
        "role": role,
        "eligible": eligible,
        "eligibility_reason": reason,
        "ageDays": age,
        "instCategory": category,
    }


def ohlcv(
    closes: list[float],
    *,
    start: pd.Timestamp = START,
    freq: str = "1D",
    volumes: list[float] | None = None,
    ranges: list[float] | None = None,
) -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes), freq=freq, tz=UTC)
    values = np.asarray(closes, dtype=float)
    width = np.asarray(ranges if ranges is not None else [1.0] * len(closes), dtype=float)
    quote = np.asarray(volumes if volumes is not None else [1_000.0] * len(closes), dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": values,
            "high": values + width / 2,
            "low": values - width / 2,
            "close": values,
            "volume": quote,
            "quote_volume": quote,
        }
    )


def rising_days(
    up_days: int = 2,
    last_volume_mult: float = 1.0,
    history: int = 40,
    step: float = 0.02,
) -> pd.DataFrame:
    base = [100.0] * history
    price = 100.0
    for _ in range(up_days):
        price *= 1 + step
        base.append(price)
    volumes = [1_000.0] * (history + up_days)
    volumes[-1] *= last_volume_mult
    return ohlcv(base, volumes=volumes)


def falling_days(down_days: int = 2, last_range: float = 1.0, history: int = 40) -> pd.DataFrame:
    base = [100.0] * history
    price = 100.0
    for _ in range(down_days):
        price *= 0.97
        base.append(price)
    ranges = [1.0] * (history + down_days)
    ranges[-1] = last_range
    return ohlcv(base, ranges=ranges)


def rising_hours(up_bars: int = 4, last_volume_mult: float = 2.0, history: int = 40) -> pd.DataFrame:
    base = [100.0] * history
    price = 100.0
    for _ in range(up_bars):
        price *= 1.01
        base.append(price)
    volumes = [1_000.0] * (history + up_bars)
    volumes[-1] *= last_volume_mult
    return ohlcv(base, start=pd.Timestamp("2025-01-01 00:00", tz=UTC), freq="4h", volumes=volumes)
