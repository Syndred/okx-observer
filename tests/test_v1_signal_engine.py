from __future__ import annotations

import pandas as pd
import unittest

from user_data.strategies.v1_signal_engine import V1Parameters, scan_v1_setups


SIX_AVERAGES = ("ma20", "ma60", "ma120", "ema20", "ema60", "ema120")


def candles_with_average_values(closes: list[float], average_values: list[float]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [price + 0.2 for price in closes],
            "low": [price - 0.2 for price in closes],
            "close": closes,
            "volume": 1.0,
        }
    )
    for column in SIX_AVERAGES:
        frame[column] = average_values
    return frame


class V1SignalEngineTests(unittest.TestCase):
    def test_does_not_emit_entries_without_a_compressed_average_cluster(self) -> None:
        frame = candles_with_average_values(
            closes=[100.0, 101.0, 102.0, 103.0],
            average_values=[90.0, 91.0, 92.0, 93.0],
        )
        for offset, column in enumerate(SIX_AVERAGES):
            frame[column] = frame[column] + offset * 3.0

        result = scan_v1_setups(frame, V1Parameters(compression_threshold=0.01))

        self.assertEqual(result["enter_long"].sum(), 0)
        self.assertEqual(result["enter_short"].sum(), 0)

    def test_enters_long_on_first_pullback_after_upside_breakout(self) -> None:
        frame = candles_with_average_values(
            closes=[100.0, 101.0, 100.5, 103.0],
            average_values=[100.0, 100.0, 100.0, 100.0],
        )
        frame.loc[2, "low"] = 100.1

        result = scan_v1_setups(
            frame,
            V1Parameters(
                compression_threshold=0.001,
                breakout_threshold=0.005,
                pullback_tolerance=0.005,
            ),
        )

        self.assertEqual(result["enter_long"].tolist(), [0, 0, 1, 0])
        self.assertEqual(result["enter_short"].sum(), 0)
        self.assertAlmostEqual(result.loc[2, "initial_stop_price"], 100.0)
        self.assertAlmostEqual(result.loc[2, "risk_distance_ratio"], 0.5 / 100.5)

    def test_enters_short_on_first_pullback_after_downside_breakout(self) -> None:
        frame = candles_with_average_values(
            closes=[100.0, 99.0, 99.5, 97.0],
            average_values=[100.0, 100.0, 100.0, 100.0],
        )
        frame.loc[2, "high"] = 99.9

        result = scan_v1_setups(
            frame,
            V1Parameters(
                compression_threshold=0.001,
                breakout_threshold=0.005,
                pullback_tolerance=0.005,
            ),
        )

        self.assertEqual(result["enter_short"].tolist(), [0, 0, 1, 0])
        self.assertEqual(result["enter_long"].sum(), 0)
        self.assertAlmostEqual(result.loc[2, "initial_stop_price"], 100.0)
        self.assertAlmostEqual(result.loc[2, "risk_distance_ratio"], 0.5 / 99.5)

    def test_breakout_candle_cannot_also_be_the_pullback_entry(self) -> None:
        frame = candles_with_average_values(
            closes=[100.0, 101.0, 103.0],
            average_values=[100.0, 100.0, 100.0],
        )
        frame.loc[1, "low"] = 99.9

        result = scan_v1_setups(
            frame,
            V1Parameters(breakout_threshold=0.005, pullback_tolerance=0.005),
        )

        self.assertEqual(result["enter_long"].sum(), 0)

    def test_first_pullback_that_fails_to_hold_cancels_the_setup(self) -> None:
        frame = candles_with_average_values(
            closes=[100.0, 101.0, 99.0, 100.4, 103.0],
            average_values=[100.0] * 5,
        )
        frame.loc[2, "low"] = 98.8
        frame.loc[3, "low"] = 100.1

        result = scan_v1_setups(
            frame,
            V1Parameters(
                compression_threshold=0.001,
                breakout_threshold=0.005,
                pullback_tolerance=0.005,
            ),
        )

        self.assertEqual(result["enter_long"].sum(), 0)

    def test_pullback_after_wait_limit_is_ignored(self) -> None:
        frame = candles_with_average_values(
            closes=[100.0, 101.0, 102.0, 102.0, 100.4],
            average_values=[100.0] * 5,
        )
        frame.loc[4, "low"] = 100.1

        result = scan_v1_setups(
            frame,
            V1Parameters(
                compression_threshold=0.001,
                breakout_threshold=0.005,
                pullback_tolerance=0.005,
                pullback_max_candles=2,
            ),
        )

        self.assertEqual(result["enter_long"].sum(), 0)

    def test_appending_future_candles_does_not_change_historical_signals(self) -> None:
        prefix = candles_with_average_values(
            closes=[100.0, 101.0, 100.5, 103.0],
            average_values=[100.0] * 4,
        )
        prefix.loc[2, "low"] = 100.1
        full = candles_with_average_values(
            closes=[100.0, 101.0, 100.5, 103.0, 70.0, 140.0],
            average_values=[100.0] * 6,
        )
        full.loc[2, "low"] = 100.1
        params = V1Parameters(breakout_threshold=0.005, pullback_tolerance=0.005)

        prefix_result = scan_v1_setups(prefix, params)
        full_result = scan_v1_setups(full, params).iloc[: len(prefix)]

        pd.testing.assert_series_equal(
            prefix_result["enter_long"], full_result["enter_long"], check_names=False
        )
        pd.testing.assert_series_equal(
            prefix_result["enter_short"], full_result["enter_short"], check_names=False
        )


if __name__ == "__main__":
    unittest.main()
