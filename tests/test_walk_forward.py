from __future__ import annotations

from datetime import date
import unittest

from user_data.strategy_lib.walk_forward import candidate_grid, candidate_score, rolling_windows


class WalkForwardTests(unittest.TestCase):
    def test_balanced_grid_covers_every_option_without_full_cartesian_cost(self) -> None:
        grid = candidate_grid()

        self.assertLessEqual(len(grid), 32)
        self.assertEqual({item.compression_atr for item in grid}, {0.4, 0.6, 0.8, 1.0})
        self.assertEqual({item.breakout_atr for item in grid}, {0.05, 0.1, 0.2, 0.3})
        self.assertEqual({item.pullback_atr for item in grid}, {0.1, 0.2, 0.3})
        self.assertEqual({item.pullback_wait_15m for item in grid}, {4, 8, 12})
        self.assertEqual({item.strict_market_consensus for item in grid}, {False, True})

    def test_exhaustive_grid_remains_available(self) -> None:
        self.assertEqual(len(candidate_grid(exhaustive=True)), 288)

    def test_windows_never_overlap_training_with_validation(self) -> None:
        windows = rolling_windows(date(2023, 1, 1), date(2026, 1, 1))

        self.assertGreater(len(windows), 1)
        for window in windows:
            self.assertLessEqual(window.train_end, window.validation_start)

    def test_windows_are_deterministic_and_end_bounded(self) -> None:
        first = rolling_windows(date(2023, 1, 31), date(2025, 7, 15))
        second = rolling_windows(date(2023, 1, 31), date(2025, 7, 15))

        self.assertEqual(first, second)
        self.assertTrue(all(window.validation_end <= date(2025, 7, 15) for window in first))

    def test_high_return_candidate_fails_excess_drawdown(self) -> None:
        result = candidate_score(
            [{"pf": 1.40, "drawdown": 0.36, "trades": 500, "stress_pf": 1.20}]
        )

        self.assertFalse(result.passed)
        self.assertIn("max_drawdown", result.failed_gates)

    def test_candidate_requires_double_cost_pf(self) -> None:
        result = candidate_score(
            [{"pf": 1.20, "drawdown": 0.20, "trades": 500, "stress_pf": 1.04}]
        )

        self.assertFalse(result.passed)
        self.assertIn("stress_pf", result.failed_gates)

    def test_candidate_requires_enough_trades_and_stable_worst_window(self) -> None:
        result = candidate_score(
            [
                {"pf": 1.30, "drawdown": 0.20, "trades": 100, "stress_pf": 1.10},
                {"pf": 0.90, "drawdown": 0.18, "trades": 100, "stress_pf": 1.08},
            ]
        )

        self.assertFalse(result.passed)
        self.assertIn("trades", result.failed_gates)
        self.assertIn("worst_window_pf", result.failed_gates)

    def test_candidate_passes_every_hard_gate(self) -> None:
        result = candidate_score(
            [
                {
                    "pf": 1.25,
                    "gross_profit": 150,
                    "gross_loss": 100,
                    "drawdown": 0.20,
                    "trades": 180,
                    "stress_pf": 1.08,
                },
                {
                    "pf": 1.10,
                    "gross_profit": 110,
                    "gross_loss": 100,
                    "drawdown": 0.25,
                    "trades": 180,
                    "stress_pf": 1.06,
                },
            ]
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.failed_gates, ())


if __name__ == "__main__":
    unittest.main()
