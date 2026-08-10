from __future__ import annotations

import unittest

from user_data.strategy_lib.v2_portfolio import (
    TradeState,
    exit_decision,
    risk_budget,
)


class V2PortfolioTests(unittest.TestCase):
    def test_first_two_trades_receive_three_quarter_percent(self) -> None:
        self.assertAlmostEqual(risk_budget([], [], "long", 0), 0.0075)
        self.assertAlmostEqual(risk_budget([0.0075], ["long"], "short", 0), 0.0075)

    def test_third_trade_receives_only_remaining_half_percent(self) -> None:
        self.assertAlmostEqual(
            risk_budget([0.0075, 0.0075], ["long", "short"], "long", 0),
            0.005,
        )

    def test_third_same_direction_trade_is_rejected(self) -> None:
        self.assertEqual(
            risk_budget([0.0075, 0.0075], ["long", "long"], "long", 0),
            0,
        )

    def test_drawdown_and_daily_loss_circuit_breakers(self) -> None:
        self.assertEqual(risk_budget([], [], "long", 0.20), 0.005)
        self.assertEqual(risk_budget([], [], "long", 0.30), 0)
        self.assertEqual(risk_budget([], [], "long", 0, daily_loss_r=4.0), 0)

    def test_two_r_reduces_forty_percent_and_moves_to_break_even(self) -> None:
        action = exit_decision(
            TradeState(
                age_hours=3,
                r_multiple=2.1,
                partial_taken=False,
                rate=102,
                entry=100,
                ema20=101,
                fees_ratio=0.001,
            )
        )

        self.assertEqual(action.reduce_fraction, 0.40)
        self.assertGreaterEqual(action.new_stop or 0, 100.1)

    def test_short_break_even_and_trail_are_directional(self) -> None:
        partial = exit_decision(
            TradeState(3, 2.1, False, 98, 100, 99, 0.001, is_short=True)
        )
        runner = exit_decision(
            TradeState(
                4,
                2.5,
                True,
                97.5,
                100,
                98.0,
                0.001,
                is_short=True,
                current_stop=99.9,
            )
        )

        self.assertLessEqual(partial.new_stop or 100, 99.9)
        self.assertEqual(runner.new_stop, 98.0)

    def test_time_and_target_exits(self) -> None:
        no_progress = exit_decision(TradeState(6, 0.4, False, 100.4, 100, 100, 0.001))
        ordinary = exit_decision(TradeState(12, 1.0, False, 101, 100, 100.5, 0.001))
        target = exit_decision(TradeState(8, 5.0, True, 105, 100, 104, 0.001))
        runner = exit_decision(TradeState(24, 2.5, True, 102.5, 100, 102, 0.001))

        self.assertEqual(no_progress.full_exit_reason, "no_progress_6h")
        self.assertEqual(ordinary.full_exit_reason, "ordinary_12h")
        self.assertEqual(target.full_exit_reason, "hard_5r")
        self.assertEqual(runner.full_exit_reason, "runner_24h")


if __name__ == "__main__":
    unittest.main()
