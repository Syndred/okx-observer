from __future__ import annotations

import unittest

import pandas as pd

from scripts.analyze_six_ma_execution_variants import scaled_events


class ScaledEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = pd.DataFrame(
            [
                {
                    "entry_date": "2025-01-01T00:00:00Z",
                    "pair": "BTC/USDT:USDT",
                    "side": "long",
                    "entry_price": 100.0,
                    "risk_price": 2.0,
                    "rank": 1,
                    "category": "BTC",
                },
                {
                    "entry_date": "2025-01-01T00:15:00Z",
                    "pair": "ETH/USDT:USDT",
                    "side": "short",
                    "entry_price": 200.0,
                    "risk_price": 3.0,
                    "rank": 2,
                    "category": "ETH",
                },
            ]
        )

    def test_stop_distance_doubles_for_both_long_and_short(self) -> None:
        events = scaled_events(self.paths, stop_multiplier=2.0, direction="both")

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].side, "long")
        self.assertEqual(events[1].side, "short")
        self.assertAlmostEqual(events[0].stop_price, 96.0)
        self.assertAlmostEqual(events[1].stop_price, 206.0)
        self.assertAlmostEqual(
            self.paths.loc[0, "entry_price"] - events[0].stop_price,
            2.0 * self.paths.loc[0, "risk_price"],
        )
        self.assertAlmostEqual(
            events[1].stop_price - self.paths.loc[1, "entry_price"],
            2.0 * self.paths.loc[1, "risk_price"],
        )

    def test_short_direction_filters_out_long_paths(self) -> None:
        events = scaled_events(self.paths, stop_multiplier=2.0, direction="short")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].pair, "ETH/USDT:USDT")
        self.assertEqual(events[0].side, "short")
        self.assertAlmostEqual(events[0].stop_price, 206.0)

    def test_non_positive_stop_multiplier_is_rejected(self) -> None:
        for multiplier in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(multiplier=multiplier):
                with self.assertRaises(ValueError):
                    scaled_events(self.paths, stop_multiplier=multiplier, direction="both")

    def test_unknown_direction_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            scaled_events(self.paths, stop_multiplier=1.0, direction="sideways")

    def test_unknown_path_side_is_rejected(self) -> None:
        paths = self.paths.copy()
        paths.loc[0, "side"] = "flat"
        with self.assertRaises(ValueError):
            scaled_events(paths, stop_multiplier=1.0, direction="both")


if __name__ == "__main__":
    unittest.main()
