from __future__ import annotations

from dataclasses import replace
import json
import math
import unittest

import pandas as pd

from user_data.strategy_lib.jev_research import build_state, calibration, event_key, select_events
from user_data.strategy_lib.v2_backtester import BacktestOptions, EntryEvent


def fixture():
    cutoff = pd.Timestamp("2025-06-01 12:00", tz="UTC")
    frames = {}
    for timeframe, frequency in {"15m": "15min", "1h": "h", "4h": "4h", "1d": "1d"}.items():
        dates = pd.date_range(end=cutoff, periods=131, freq=frequency)
        values = [100 + index / 10 for index in range(131)]
        frames[timeframe] = pd.DataFrame({
            "date": dates, "open": values, "high": [v + 1 for v in values],
            "low": [v - 1 for v in values], "close": values, "volume": 100,
        })
    return EntryEvent(cutoff, "SECRET/USDT:USDT", "long", 108, 1), frames


class JevResearchTests(unittest.TestCase):
    def test_future_candles_and_cached_indicators_cannot_contaminate_state(self):
        event, frames = fixture()
        baseline = build_state(event, frames, BacktestOptions(), {"fast": 20})
        modified = {key: value.copy() for key, value in frames.items()}
        for timeframe, frame in modified.items():
            frame.loc[frame["date"] >= event.date, ["open", "close", "high", "low", "volume"]] = 999999
            frame["ma20"] = 987654
            frame["trend_long"] = False
        self.assertEqual(baseline, build_state(event, modified, BacktestOptions(), {"fast": 20}))
        serialized = json.dumps(baseline, allow_nan=False)
        self.assertNotIn(event.pair, serialized)
        self.assertNotIn("2025-", serialized)
        self.assertNotIn("999999", serialized)

    def test_unfinished_high_timeframe_is_excluded(self):
        event, frames = fixture()
        state = build_state(event, frames, BacktestOptions(), {})
        for timeframe, duration in {"1h": "1h", "4h": "4h", "1d": "1d"}.items():
            new = frames[timeframe].iloc[-1:].copy()
            new["date"] = event.date - pd.Timedelta(duration) / 2
            new[["open", "high", "low", "close", "volume"]] = 987654
            frames[timeframe] = pd.concat([frames[timeframe], new], ignore_index=True)
        self.assertEqual(state, build_state(event, frames, BacktestOptions(), {}))

    def test_price_and_volume_scale_invariance(self):
        event, frames = fixture()
        original = build_state(event, frames, BacktestOptions(), {})
        for frame in frames.values():
            frame[["open", "high", "low", "close"]] *= 10
            frame["volume"] *= 13
        scaled = build_state(replace(event, stop_price=event.stop_price * 10), frames, BacktestOptions(), {})
        self.assertAlmostEqual(original["stop_distance_fraction"], scaled["stop_distance_fraction"])
        self.assertEqual(original["timeframes"]["15m"]["bars"][-1]["close"], 0)
        for timeframe in frames:
            for first, second in zip(original["timeframes"][timeframe]["bars"], scaled["timeframes"][timeframe]["bars"]):
                for key in first:
                    self.assertAlmostEqual(first[key], second[key])

    def test_missing_base_history_or_last_bar_is_rejected(self):
        event, frames = fixture()
        frames["15m"] = frames["15m"].iloc[-120:]
        with self.assertRaises(ValueError):
            build_state(event, frames, BacktestOptions(), {})
        event, frames = fixture()
        frames["15m"] = frames["15m"].loc[frames["15m"]["date"] != event.date - pd.Timedelta(minutes=15)]
        with self.assertRaises(ValueError):
            build_state(event, frames, BacktestOptions(), {})

    def test_execution_description_exposes_actual_time_exit_and_entry_risk(self):
        event, frames = fixture()
        options = BacktestOptions(exit_mode="fixed3", time_mode="24h")
        state = build_state(event, frames, options, {})
        description = state["execution_rules"]["description"]
        self.assertIn("24h15m elapsed", description)
        self.assertIn("15m bar opening 24 hours", description)
        self.assertIn("eventual entry fill and that fixed stop", description)
        self.assertIn("adjusted adversely by slippage_rate", description)
        self.assertEqual(state["execution_rules"]["options"]["time_mode"], "24h")
        reference = float(frames["15m"].iloc[-2]["close"])
        self.assertAlmostEqual(reference * (1 - state["stop_distance_fraction"]), event.stop_price)
        short = replace(event, side="short", stop_price=120)
        short_state = build_state(short, frames, options, {})
        self.assertAlmostEqual(reference * (1 + short_state["stop_distance_fraction"]), short.stop_price)

    def test_sparse_high_timeframe_features_are_null(self):
        event, frames = fixture()
        frames["4h"] = frames["4h"].tail(10)
        del frames["1d"]
        state = build_state(event, frames, BacktestOptions(), {})
        self.assertIsNone(state["timeframes"]["4h"]["indicators"]["ma120"])
        self.assertIsNone(state["timeframes"]["4h"]["indicators"]["compression_atr"])
        self.assertIsNone(state["timeframes"]["1d"]["indicators"])
        json.dumps(state, allow_nan=False)

    def test_selection_and_key_are_stable(self):
        event, _ = fixture()
        events = [replace(event, date=event.date + pd.Timedelta(hours=index)) for index in range(10)]
        self.assertEqual(select_events(list(reversed(events)), 3), [events[0], events[4], events[9]])
        self.assertEqual(select_events(list(reversed(events)), 0), events)
        self.assertEqual(select_events(events, 1), [events[5]])
        self.assertEqual(event_key(event), event_key(replace(event, date=event.date.tz_convert("Asia/Shanghai"))))
        with self.assertRaises(ValueError):
            select_events(events, -1)

    def test_metrics_and_empty_bins(self):
        result = calibration([0.2, 0.8], [False, True])
        self.assertEqual(result["n"], 2)
        self.assertEqual(result["win_rate"], 0.5)
        self.assertAlmostEqual(result["brier"], 0.04)
        self.assertAlmostEqual(result["log_loss"], -math.log(0.8))
        self.assertEqual(result["accuracy"], 1)
        self.assertLess(result["wilson95"]["low"], 0.5)
        self.assertGreater(result["wilson95"]["high"], 0.5)
        self.assertIsNone(result["calibration_bins"][0]["win_rate"])
        endpoints = calibration([0, 1], [False, True])
        self.assertEqual(endpoints["calibration_bins"][-1]["n"], 1)
        self.assertTrue(math.isfinite(calibration([1, 0], [False, True])["log_loss"]))
        empty = calibration([], [])
        self.assertEqual(empty["n"], 0)
        for key in ("win_rate", "brier", "log_loss", "accuracy", "mean_probability", "wilson95"):
            self.assertIsNone(empty[key])
        for probabilities, outcomes in (([0.1], []), ([float("nan")], [1]), ([2], [1]), ([0.2], [2])):
            with self.assertRaises(ValueError):
                calibration(probabilities, outcomes)


if __name__ == "__main__":
    unittest.main()
