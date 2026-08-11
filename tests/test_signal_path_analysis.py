from __future__ import annotations

import unittest

import pandas as pd

from user_data.strategy_lib.signal_path_analysis import analyze_signal_paths
from user_data.strategy_lib.v2_backtester import EntryEvent


UTC = "UTC"
START = pd.Timestamp("2025-01-01 00:00", tz=UTC)


class SignalPathAnalysisTests(unittest.TestCase):
    def test_long_and_short_mfe_mae_and_target_paths_are_risk_normalized(self) -> None:
        frames = {
            "LONG": path_frame("long"),
            "SHORT": path_frame("short"),
        }
        events = [
            EntryEvent(START, "LONG", "long", 98.0, 1),
            EntryEvent(START, "SHORT", "short", 102.0, 1),
        ]

        result = analyze_signal_paths(frames, events)

        self.assertEqual(len(result), 2)
        long = result.loc[result["pair"] == "LONG"].iloc[0]
        short = result.loc[result["pair"] == "SHORT"].iloc[0]
        for row in (long, short):
            self.assertTrue(bool(row["complete_window"]))
            self.assertFalse(bool(row["stop_hit"]))
            self.assertTrue(bool(row["target_1r_before_stop"]))
            self.assertTrue(bool(row["target_2r_before_stop"]))
            self.assertFalse(bool(row["target_3r_before_stop"]))
            self.assertFalse(bool(row["target_5r_before_stop"]))

        self.assertAlmostEqual(float(long["return_1h_r"]), 1.5)
        self.assertAlmostEqual(float(long["mfe_1h_r"]), 2.0)
        self.assertAlmostEqual(float(long["mae_1h_r"]), 0.5)
        self.assertAlmostEqual(float(short["return_1h_r"]), 1.5)
        self.assertAlmostEqual(float(short["mfe_1h_r"]), 2.0)
        self.assertAlmostEqual(float(short["mae_1h_r"]), 0.5)

    def test_same_candle_stop_has_priority_over_targets(self) -> None:
        frame = path_frame("long")
        frame.loc[1, ["high", "low", "close"]] = [110.0, 97.0, 100.0]
        event = EntryEvent(START, "LONG", "long", 98.0, 1)

        result = analyze_signal_paths({"LONG": frame}, [event]).iloc[0]

        self.assertTrue(bool(result["stop_hit"]))
        self.assertEqual(result["stop_time"], START + pd.Timedelta(minutes=15))
        for target in (1, 2, 3, 5):
            self.assertFalse(bool(result[f"target_{target}r_before_stop"]))
        self.assertFalse(bool(result["stopped_then_3r"]))
        self.assertFalse(bool(result["stopped_then_5r"]))

    def test_targets_reached_before_a_later_stop_are_retained(self) -> None:
        frame = path_frame("long")
        frame.loc[1, ["high", "low", "close"]] = [104.0, 99.0, 103.0]
        frame.loc[2, ["high", "low", "close"]] = [101.0, 97.0, 99.0]
        event = EntryEvent(START, "LONG", "long", 98.0, 1)

        result = analyze_signal_paths({"LONG": frame}, [event]).iloc[0]

        self.assertTrue(bool(result["stop_hit"]))
        self.assertEqual(result["stop_time"], START + pd.Timedelta(minutes=30))
        self.assertTrue(bool(result["target_1r_before_stop"]))
        self.assertTrue(bool(result["target_2r_before_stop"]))
        self.assertFalse(bool(result["target_3r_before_stop"]))
        self.assertFalse(bool(result["target_5r_before_stop"]))

    def test_stopped_then_3r_and_5r_are_reported_separately(self) -> None:
        frame = path_frame("long")
        frame.loc[1, ["high", "low", "close"]] = [99.0, 97.0, 98.0]
        frame.loc[2, ["high", "low", "close"]] = [106.0, 99.0, 105.0]
        frame.loc[3, ["high", "low", "close"]] = [110.0, 99.0, 109.0]
        event = EntryEvent(START, "LONG", "long", 98.0, 1)

        result = analyze_signal_paths({"LONG": frame}, [event]).iloc[0]

        self.assertTrue(bool(result["stop_hit"]))
        self.assertFalse(bool(result["target_1r_before_stop"]))
        self.assertFalse(bool(result["target_2r_before_stop"]))
        self.assertFalse(bool(result["target_3r_before_stop"]))
        self.assertFalse(bool(result["target_5r_before_stop"]))
        self.assertTrue(bool(result["stopped_then_3r"]))
        self.assertTrue(bool(result["stopped_then_5r"]))

    def test_future_rows_do_not_change_a_completed_24h_sample(self) -> None:
        frame = path_frame("long")
        event = EntryEvent(START, "LONG", "long", 98.0, 1)
        before = analyze_signal_paths({"LONG": frame}, [event])

        future = frame.iloc[-1:].copy()
        future["date"] = future["date"] + pd.Timedelta(minutes=15)
        future[["open", "high", "low", "close"]] = [100.0, 1_000.0, 1.0, 500.0]
        extended = pd.concat([frame, future], ignore_index=True)
        after = analyze_signal_paths({"LONG": extended}, [event])

        pd.testing.assert_frame_equal(before, after)
        self.assertTrue(bool(after.loc[0, "complete_window"]))

    def test_missing_15m_candle_marks_window_incomplete(self) -> None:
        frame = path_frame("long").drop(index=10).reset_index(drop=True)
        event = EntryEvent(START, "LONG", "long", 98.0, 1)

        result = analyze_signal_paths({"LONG": frame}, [event]).iloc[0]

        self.assertFalse(bool(result["complete_window"]))
        self.assertTrue(pd.isna(result["return_24h_r"]))


def path_frame(side: str) -> pd.DataFrame:
    dates = pd.date_range(START, periods=97, freq="15min", tz=UTC)
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": [100.0] * len(dates),
            "high": [100.2] * len(dates),
            "low": [99.8] * len(dates),
            "close": [100.0] * len(dates),
            "volume": [1.0] * len(dates),
        }
    )
    if side == "long":
        frame.loc[3, ["high", "low", "close"]] = [104.0, 99.0, 103.0]
    else:
        frame.loc[3, ["high", "low", "close"]] = [101.0, 96.0, 97.0]
    return frame


if __name__ == "__main__":
    unittest.main()
