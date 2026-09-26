from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from scripts.run_jev_ma_research import (
    eligible_events, independent_outcome, load_key_file, predict,
)
from user_data.strategy_lib.jev_provider import JevProviderError
from user_data.strategy_lib.v2_backtester import BacktestOptions, EntryEvent


class PredictionCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name) / "cache"
        self.client = Mock(model="jev-1.13.0")
        self.client.evaluate.return_value = {
            "model": "jev-1.13.0", "probability": 0.7, "confidence": 0.8,
        }
        self.state = {"execution_rules": {"max_holding_hours": 24}, "feature": 0.1}

    def cached_path(self):
        return next(self.cache.glob("*.json"))

    def test_second_identical_request_uses_persistent_cache(self):
        first, first_hit = predict(self.client, self.state, self.cache)
        second, second_hit = predict(self.client, dict(reversed(list(self.state.items()))), self.cache)
        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertEqual(first, second)
        self.assertEqual(len(first["request_hash"]), 64)
        self.client.evaluate.assert_called_once_with(self.state)
        self.assertEqual(list(self.cache.glob("*.tmp")), [])

    def test_changed_state_or_model_misses_cache(self):
        predict(self.client, self.state, self.cache)
        _, hit = predict(self.client, dict(self.state, feature=0.2), self.cache)
        self.assertFalse(hit)
        self.client.model = "jev-another-version"
        self.client.evaluate.return_value["model"] = "jev-another-version"
        _, hit = predict(self.client, self.state, self.cache)
        self.assertFalse(hit)
        self.assertEqual(self.client.evaluate.call_count, 3)

    def test_cache_hash_mismatch_fails_without_network(self):
        answer, _ = predict(self.client, self.state, self.cache)
        answer["request_hash"] = "wrong"
        self.cached_path().write_text(json.dumps(answer))
        with self.assertRaises(ValueError):
            predict(self.client, self.state, self.cache)
        self.assertEqual(self.client.evaluate.call_count, 1)

    def test_cached_invalid_probabilities_are_not_accepted(self):
        original, _ = predict(self.client, self.state, self.cache)
        for value in (True, "0.7", -0.1, 1.1, None, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.cached_path().write_text(json.dumps(dict(original, probability=value)))
                with self.assertRaises(ValueError):
                    predict(self.client, self.state, self.cache)
        self.assertEqual(self.client.evaluate.call_count, 1)

    def test_cached_invalid_model_and_confidence_are_not_accepted(self):
        original, _ = predict(self.client, self.state, self.cache)
        variants = [dict(original, model=value) for value in ("", " ", 12, None)]
        variants += [dict(original, confidence=value) for value in (True, "0.8", -0.1, 1.1, float("nan"))]
        for value in variants:
            with self.subTest(value=value):
                self.cached_path().write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    predict(self.client, self.state, self.cache)
        self.assertEqual(self.client.evaluate.call_count, 1)

    def test_provider_failure_does_not_create_success_cache(self):
        self.client.evaluate.side_effect = JevProviderError("timeout")
        with self.assertRaises(JevProviderError):
            predict(self.client, self.state, self.cache)
        self.assertEqual(list(self.cache.glob("*.json")), [])


class EventEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.start = pd.Timestamp("2026-01-03", tz="UTC")
        self.end = self.start + pd.Timedelta(days=3)
        dates = pd.date_range(self.start - pd.Timedelta(days=3), self.end, freq="15min")
        self.frames = {"A": pd.DataFrame({"date": dates, "close": 100.0})}

    def event(self, date):
        return EntryEvent(date, "A", "long", 99.0, 1)

    def test_start_included_and_last_24_hours_excluded(self):
        moments = [self.start - pd.Timedelta(minutes=15), self.start,
                   self.end - pd.Timedelta(hours=24, minutes=15),
                   self.end - pd.Timedelta(hours=24), self.end]
        events = [self.event(date) for date in moments]
        self.assertEqual(eligible_events(events, self.frames, self.start, self.end), events[1:3])

    def test_missing_past_entry_or_future_candle_excludes_event(self):
        event = self.event(self.start)
        for gap in (self.start - pd.Timedelta(hours=30), self.start,
                    self.start + pd.Timedelta(hours=12), self.start + pd.Timedelta(hours=24)):
            with self.subTest(gap=gap):
                frames = {"A": self.frames["A"].loc[self.frames["A"]["date"] != gap]}
                self.assertEqual(eligible_events([event], frames, self.start, self.end), [])

    def test_selection_depends_on_availability_not_future_return(self):
        events = [self.event(self.start)]
        expected = eligible_events(events, self.frames, self.start, self.end)
        self.frames["A"].loc[self.frames["A"]["date"] >= self.start, "close"] = -999.0
        self.assertEqual(eligible_events(events, self.frames, self.start, self.end), expected)


class IndependentOutcomeTests(unittest.TestCase):
    def test_costs_turn_small_gross_gain_into_net_loss(self):
        at = pd.Timestamp("2026-01-01", tz="UTC")
        dates = pd.date_range(at, periods=97, freq="15min")
        frame = pd.DataFrame({"date": dates, "open": 100.0, "high": 100.08,
                              "low": 99.98, "close": 100.02, "volume": 1.0})
        event = EntryEvent(at, "A", "long", 99.0, 1)
        frames = {"A": frame}
        funding = {"A": pd.DataFrame(columns=["date", "rate"])}
        options = BacktestOptions(exit_mode="fixed3", time_mode="24h", fee_rate=0,
                                  slippage_rate=0, missing_funding_rate_per_8h=0)
        gross = independent_outcome(event, frames, funding, options)
        net = independent_outcome(event, frames, funding, replace(options, fee_rate=.0005, slippage_rate=.0005))
        self.assertGreater(gross.net_pnl, 0)
        self.assertLess(net.net_pnl, 0)
        self.assertGreater(net.fees, 0)
        self.assertEqual(net.exit_reason, "all_24h")

    def test_no_execution_is_an_error_not_a_fake_loss(self):
        at = pd.Timestamp("2026-01-01", tz="UTC")
        frame = pd.DataFrame({"date": [at], "open": [100.0], "high": [101.0],
                              "low": [99.0], "close": [100.0], "volume": [1.0]})
        event = EntryEvent(at, "A", "long", 110.0, 1)
        with self.assertRaisesRegex(ValueError, "exactly one trade"):
            independent_outcome(event, {"A": frame}, {"A": pd.DataFrame(columns=["date", "rate"])},
                                BacktestOptions(exit_mode="fixed3", time_mode="24h"))


class KeyFileTests(unittest.TestCase):
    def test_only_keys_loaded_existing_environment_preserved_and_no_shell_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keys.env"
            path.write_text("TYPESAFE_API_KEY='file-key'\nJEV_API_KEY=\"jev-key\"\nOTHER=ignored\n"
                            "export ALSO=ignored\n# TYPESAFE_API_KEY=commented\n"
                            "SHELL_VALUE=$(touch should-not-exist)\n")
            with patch.dict(os.environ, {"TYPESAFE_API_KEY": "existing"}, clear=True):
                load_key_file(path)
                self.assertEqual(dict(os.environ), {"TYPESAFE_API_KEY": "existing", "JEV_API_KEY": "jev-key"})
            with patch.dict(os.environ, {}, clear=True):
                load_key_file(path)
                self.assertEqual(os.environ["TYPESAFE_API_KEY"], "file-key")
                self.assertEqual(os.environ["JEV_API_KEY"], "jev-key")

    def test_no_file_is_noop(self):
        with patch.dict(os.environ, {}, clear=True):
            load_key_file(None)
            self.assertEqual(dict(os.environ), {})


if __name__ == "__main__":
    unittest.main()
