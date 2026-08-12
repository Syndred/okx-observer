from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from user_data.strategy_lib.okx_trend_compression_screener import (
    ScreenerParameters,
    screen_instruments,
    trend_snapshot,
)


UTC = "UTC"
PAIR = "ALT-USDT-SWAP"
START = pd.Timestamp("2025-01-01 00:00", tz=UTC)
SIX = ("ma20", "ma60", "ma120", "ema20", "ema60", "ema120")


class OkxTrendCompressionScreenerTests(unittest.TestCase):
    def test_long_trend_requires_close_ema_order_and_positive_slope(self) -> None:
        frame = trend_frame("long")

        snapshot = trend_snapshot(frame)

        self.assertEqual(snapshot["direction"], "long")
        self.assertTrue(bool(snapshot["strict_six"]))

        for column, value in (
            ("close", 104.0),
            ("ema20", 99.0),
            ("ema60_slope3", -1.0),
        ):
            invalid = frame.copy()
            invalid.loc[0, column] = value
            with self.subTest(column=column):
                self.assertEqual(trend_snapshot(invalid)["direction"], "neutral")

    def test_short_trend_is_the_mirror_of_long(self) -> None:
        frame = trend_frame("short")

        snapshot = trend_snapshot(frame)

        self.assertEqual(snapshot["direction"], "short")
        self.assertTrue(bool(snapshot["strict_six"]))

        invalid_close = frame.copy()
        invalid_close.loc[0, "close"] = 96.0
        invalid_slope = frame.copy()
        invalid_slope.loc[0, "ema60_slope3"] = 1.0

        self.assertEqual(trend_snapshot(invalid_close)["direction"], "neutral")
        self.assertEqual(trend_snapshot(invalid_slope)["direction"], "neutral")

    def test_daily_and_four_hour_trends_must_align(self) -> None:
        daily = trend_frame("long")
        four_hour = trend_frame("long")
        compressed = compression_frame("long", spread=1.0)

        aligned = screen_one(daily, four_hour, compressed)
        self.assertTrue(bool(aligned["candidate"]))
        self.assertEqual(aligned["direction"], "long")
        self.assertEqual(aligned["status"], "candidate")

        opposed = screen_one(daily, trend_frame("short"), compressed)
        self.assertFalse(bool(opposed["candidate"]))
        self.assertEqual(opposed["status"], "daily_4h_not_aligned")

    def test_compression_threshold_is_inclusive_and_uses_atr(self) -> None:
        at_threshold = screen_one(
            trend_frame("long"),
            trend_frame("long"),
            compression_frame("long", spread=4.0, atr=2.0),
        )
        above_threshold = screen_one(
            trend_frame("long"),
            trend_frame("long"),
            compression_frame("long", spread=4.02, atr=2.0),
        )

        self.assertAlmostEqual(float(at_threshold["compression_15m_atr"]), 2.0)
        self.assertTrue(bool(at_threshold["candidate"]))
        self.assertFalse(bool(above_threshold["candidate"]))
        self.assertEqual(above_threshold["status"], "aligned_waiting_for_compression")

    def test_invalid_atr_or_missing_six_line_is_insufficient_15m_data(self) -> None:
        invalid_atr = compression_frame("long", spread=1.0, atr=0.0)
        missing_line = compression_frame("long", spread=1.0)
        missing_line.loc[0, "ema120"] = np.nan

        for frame in (invalid_atr, missing_line):
            with self.subTest(frame=frame):
                result = screen_one(trend_frame("long"), trend_frame("long"), frame)
                self.assertFalse(bool(result["candidate"]))
                self.assertEqual(result["status"], "insufficient_15m_data")

    def test_empty_daily_four_hour_or_15m_data_is_marked(self) -> None:
        cases = (
            ("insufficient_daily_data", pd.DataFrame(), trend_frame("long"), compression_frame("long")),
            ("insufficient_4h_data", trend_frame("long"), pd.DataFrame(), compression_frame("long")),
            ("insufficient_15m_data", trend_frame("long"), trend_frame("long"), pd.DataFrame()),
        )

        for expected_status, daily, four_hour, compressed in cases:
            with self.subTest(expected_status=expected_status):
                result = screen_one(daily, four_hour, compressed)
                self.assertFalse(bool(result["candidate"]))
                self.assertEqual(result["status"], expected_status)

    def test_listing_age_boundary_and_recent_listing_status(self) -> None:
        recent = screen_one(
            trend_frame("long"),
            trend_frame("long"),
            compression_frame("long"),
            age_days=29,
            eligibility_reason="listed_less_than_30_days",
        )
        exactly_old_enough = screen_one(
            trend_frame("long"),
            trend_frame("long"),
            compression_frame("long"),
            age_days=30,
        )

        self.assertFalse(bool(recent["candidate"]))
        self.assertEqual(recent["status"], "listed_too_recently")
        self.assertTrue(bool(exactly_old_enough["candidate"]))

    def test_unknown_category_is_not_a_trade_candidate(self) -> None:
        result = screen_one(
            trend_frame("long"),
            trend_frame("long"),
            compression_frame("long"),
            eligibility_reason="unknown_category",
        )

        self.assertFalse(bool(result["candidate"]))
        self.assertEqual(result["status"], "unknown_category")

    def test_short_and_long_candidates_have_symmetric_compression_metrics(self) -> None:
        long_result = screen_one(
            trend_frame("long"), trend_frame("long"), compression_frame("long", spread=1.0)
        )
        short_result = screen_one(
            trend_frame("short"), trend_frame("short"), compression_frame("short", spread=1.0)
        )

        self.assertTrue(bool(long_result["candidate"]))
        self.assertTrue(bool(short_result["candidate"]))
        self.assertEqual(long_result["direction"], "long")
        self.assertEqual(short_result["direction"], "short")
        self.assertAlmostEqual(
            float(long_result["compression_15m_atr"]),
            float(short_result["compression_15m_atr"]),
        )
        self.assertAlmostEqual(
            float(long_result["daily_strength_atr"]),
            float(short_result["daily_strength_atr"]),
        )

    def test_persistent_compression_produces_one_row_per_instrument_and_stable_sort(self) -> None:
        instruments = [instrument("BBB"), instrument("AAA"), instrument("WAIT")]
        daily = {instrument["instId"]: trend_frame("long", rows=3) for instrument in instruments}
        four_hour = {instrument["instId"]: trend_frame("long", rows=3) for instrument in instruments}
        fifteen = {
            "BBB-USDT-SWAP": compression_frame("long", spread=2.0, rows=3),
            "AAA-USDT-SWAP": compression_frame("long", spread=1.0, rows=3),
            "WAIT-USDT-SWAP": compression_frame("long", spread=5.0, rows=3),
        }

        result = screen_instruments(instruments, daily, four_hour, fifteen)

        self.assertEqual(result["instrument"].tolist(), ["AAA-USDT-SWAP", "BBB-USDT-SWAP", "WAIT-USDT-SWAP"])
        self.assertEqual(result["instrument"].nunique(), len(instruments))
        self.assertEqual(int(result["candidate"].sum()), 2)
        self.assertEqual(result.loc[0, "status"], "candidate")
        self.assertEqual(result.loc[1, "status"], "candidate")
        self.assertEqual(result.loc[2, "status"], "aligned_waiting_for_compression")

    def test_future_rows_do_not_change_a_snapshot_cut_at_the_same_as_of(self) -> None:
        daily = trend_frame("long")
        four_hour = trend_frame("long")
        compressed = compression_frame("long")
        as_of = START
        future_daily = trend_frame("short", date=START + pd.Timedelta(days=1))
        future_four_hour = trend_frame("short", date=START + pd.Timedelta(hours=4))
        future_15m = compression_frame("long", date=START + pd.Timedelta(minutes=15), spread=6.0)

        before = screen_one(daily, four_hour, compressed)
        after = screen_one(
            pd.concat([daily, future_daily], ignore_index=True).loc[lambda frame: frame["date"] <= as_of],
            pd.concat([four_hour, future_four_hour], ignore_index=True).loc[lambda frame: frame["date"] <= as_of],
            pd.concat([compressed, future_15m], ignore_index=True).loc[lambda frame: frame["date"] <= as_of],
        )

        stable_columns = [
            "instrument",
            "direction",
            "status",
            "candidate",
            "age_days",
            "daily_trend",
            "4h_trend",
            "compression_15m_atr",
            "last_price",
        ]
        pd.testing.assert_frame_equal(
            before.loc[stable_columns].to_frame().T.reset_index(drop=True),
            after.loc[stable_columns].to_frame().T.reset_index(drop=True),
        )

    def test_parameter_thresholds_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            screen_one(
                trend_frame("long"),
                trend_frame("long"),
                compression_frame("long"),
                params=ScreenerParameters(compression_15m_atr=0.0),
            )
        with self.assertRaises(ValueError):
            screen_one(
                trend_frame("long"),
                trend_frame("long"),
                compression_frame("long"),
                params=ScreenerParameters(min_age_days=-1),
            )


def screen_one(
    daily: pd.DataFrame,
    four_hour: pd.DataFrame,
    fifteen_minute: pd.DataFrame,
    *,
    age_days: int = 40,
    eligibility_reason: str = "eligible",
    params: ScreenerParameters | None = None,
) -> pd.Series:
    result = screen_instruments(
        [instrument("ALT", age_days=age_days, eligibility_reason=eligibility_reason)],
        {PAIR: daily},
        {PAIR: four_hour},
        {PAIR: fifteen_minute},
        params,
    )
    return result.iloc[0]


def instrument(
    base: str,
    *,
    age_days: int = 40,
    eligibility_reason: str = "eligible",
) -> dict[str, object]:
    return {
        "instId": f"{base}-USDT-SWAP",
        "base": base,
        "ageDays": age_days,
        "eligible": eligibility_reason == "eligible",
        "eligibility_reason": eligibility_reason,
        "role": "trade",
        "state": "live",
    }


def trend_frame(
    direction: str,
    *,
    date: pd.Timestamp = START,
    rows: int = 1,
) -> pd.DataFrame:
    dates = pd.date_range(date, periods=rows, freq="15min", tz=UTC)
    if direction == "long":
        values = {
            "close": 110.0,
            "ema20": 105.0,
            "ema60": 103.0,
            "ema120": 101.0,
            "ema60_slope3": 1.0,
            "ma20": 104.0,
            "ma60": 102.0,
            "ma120": 100.0,
        }
    elif direction == "short":
        values = {
            "close": 90.0,
            "ema20": 95.0,
            "ema60": 97.0,
            "ema120": 99.0,
            "ema60_slope3": -1.0,
            "ma20": 96.0,
            "ma60": 98.0,
            "ma120": 100.0,
        }
    else:
        raise ValueError(direction)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [values["close"]] * rows,
            "high": [values["close"] + 0.2] * rows,
            "low": [values["close"] - 0.2] * rows,
            "close": [values["close"]] * rows,
            "volume": [1.0] * rows,
            "atr14": [2.0] * rows,
            **{column: [values[column]] * rows for column in SIX},
            "ema60_slope3": [values["ema60_slope3"]] * rows,
        }
    )


def compression_frame(
    direction: str,
    *,
    spread: float = 1.0,
    atr: float = 2.0,
    date: pd.Timestamp = START,
    rows: int = 1,
) -> pd.DataFrame:
    dates = pd.date_range(date, periods=rows, freq="15min", tz=UTC)
    if direction == "long":
        averages = np.linspace(104.0, 104.0 + spread, len(SIX))
        close = 110.0
    elif direction == "short":
        averages = np.linspace(96.0, 96.0 - spread, len(SIX))
        close = 90.0
    else:
        raise ValueError(direction)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [close] * rows,
            "high": [close + 0.2] * rows,
            "low": [close - 0.2] * rows,
            "close": [close] * rows,
            "volume": [1.0] * rows,
            "atr14": [atr] * rows,
            **{column: [float(averages[index])] * rows for index, column in enumerate(SIX)},
        }
    )


if __name__ == "__main__":
    unittest.main()
