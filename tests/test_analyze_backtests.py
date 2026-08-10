from __future__ import annotations

import unittest
from pathlib import Path

from scripts.analyze_backtests import equity_curve, scenario_from_path, summarize_strategy


class AnalyzeBacktestsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = {
            "starting_balance": 100.0,
            "final_balance": 107.0,
            "total_trades": 3,
            "wins": 2,
            "losses": 1,
            "winrate": 2 / 3,
            "profit_factor": 2.75,
            "max_drawdown_account": 0.10,
            "max_consecutive_losses": 1,
            "trade_count_long": 2,
            "trade_count_short": 1,
            "trades": [
                {"close_timestamp": 3, "profit_abs": 5.0},
                {"close_timestamp": 1, "profit_abs": -2.0},
                {"close_timestamp": 2, "profit_abs": 4.0},
            ],
        }

    def test_summary_calculates_average_win_loss_and_payoff(self) -> None:
        row = summarize_strategy(self.result, "sample")

        self.assertEqual(row["average_win_usdt"], 4.5)
        self.assertEqual(row["average_loss_usdt"], -2.0)
        self.assertEqual(row["payoff_ratio"], 2.25)
        self.assertEqual(row["return_pct"], 7.0)
        self.assertEqual(row["maximum_balance"], 107.0)

    def test_equity_curve_orders_trades_by_close_time(self) -> None:
        curve = equity_curve(self.result)

        self.assertEqual([point[1] for point in curve], [98.0, 102.0, 107.0])

    def test_scenario_is_read_from_freqtrade_prefixed_filename(self) -> None:
        path = Path("candidate-validation/epoch-49-2026-08-10_10-14-00.zip")

        self.assertEqual(scenario_from_path(path), "epoch-49")


if __name__ == "__main__":
    unittest.main()
