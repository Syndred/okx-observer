from __future__ import annotations

import unittest

import pandas as pd

from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
    summarize_backtest,
)


def frame(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=len(closes), freq="15min", tz="UTC"),
            "open": [100.0] * len(closes),
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1.0,
            "ema20": closes,
        }
    )


class V2BacktesterTests(unittest.TestCase):
    def test_same_candle_stop_wins_over_profit_target(self) -> None:
        candles = {"A": frame([103.2, 100], [98.8, 99.5], [100, 100])}
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "long", 99, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(exit_mode="fixed3", fee_rate=0, slippage_rate=0),
        )

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_reason, "initial_stop")
        self.assertLess(result.trades[0].net_pnl, 0)

    def test_gap_through_stop_fills_at_worse_open_price(self) -> None:
        candles = {"A": frame([100.2, 98.2], [99.8, 97.5], [100, 98])}
        candles["A"].loc[1, "open"] = 98.0
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "long", 99, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(fee_rate=0, slippage_rate=0),
        )

        self.assertEqual(result.trades[0].exit_price, 98.0)

    def test_portfolio_caps_three_positions_and_two_per_direction(self) -> None:
        candles = {
            pair: frame([100.2, 100.2], [99.8, 99.8], [100, 100])
            for pair in ("A", "B", "C", "D")
        }
        at = pd.Timestamp("2025-01-01", tz="UTC")
        events = [
            EntryEvent(at, "A", "long", 95, 1),
            EntryEvent(at, "B", "long", 95, 2),
            EntryEvent(at, "C", "short", 105, 3),
            EntryEvent(at, "D", "long", 95, 4),
        ]

        result = simulate_portfolio(
            candles,
            events,
            BacktestOptions(fee_rate=0, slippage_rate=0, min_notional=0),
        )

        self.assertEqual(len(result.trades), 3)
        self.assertEqual({trade.pair for trade in result.trades}, {"A", "B", "C"})
        self.assertEqual(result.max_concurrent_positions, 3)

    def test_entry_is_skipped_when_stop_is_beyond_liquidation_buffer(self) -> None:
        candles = {"A": frame([100.2, 100.2], [99.8, 99.8], [100, 100])}
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "long", 80, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(leverage=10, fee_rate=0, slippage_rate=0, min_notional=0),
        )

        self.assertEqual(result.trades, [])
        self.assertEqual(result.skipped_entries, 1)

    def test_hybrid_takes_forty_percent_then_protects_runner(self) -> None:
        candles = {
            "A": frame(
                [100.2, 102.2, 102.1],
                [99.8, 100.5, 100.0],
                [100.0, 102.0, 100.2],
            )
        }
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "long", 99, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(fee_rate=0, slippage_rate=0),
        )

        self.assertEqual(result.trades[0].exit_reason, "trailing_stop")
        self.assertGreater(result.trades[0].net_pnl, 0)
        self.assertTrue(result.trades[0].partial_taken)

    def test_positive_funding_is_paid_by_long_position(self) -> None:
        candles = {"A": frame([100.2, 100.2], [99.8, 99.8], [100, 100])}
        funding = {
            "A": pd.DataFrame(
                {
                    "date": [pd.Timestamp("2025-01-01 00:15", tz="UTC")],
                    "rate": [0.01],
                }
            )
        }
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "long", 95, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(fee_rate=0, slippage_rate=0, min_notional=0),
            funding_frames=funding,
        )

        self.assertAlmostEqual(result.trades[0].funding, 0.15)
        self.assertAlmostEqual(result.trades[0].net_pnl, -0.15)

    def test_summary_reports_holding_time_in_hours(self) -> None:
        candles = {"A": frame([100.2, 100.2, 100.2], [99.8, 99.8, 99.8], [100, 100, 100])}
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "long", 95, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(fee_rate=0, slippage_rate=0, min_notional=0),
        )
        summary = summarize_backtest(result)

        self.assertAlmostEqual(summary["average_holding_hours"], 0.5)
        self.assertAlmostEqual(summary["median_holding_hours"], 0.5)

    def test_missing_funding_is_charged_conservatively_every_eight_hours(self) -> None:
        candles = {"A": frame([100.2] * 33, [99.8] * 33, [100] * 33)}
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "short", 105, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(
                fee_rate=0,
                slippage_rate=0,
                min_notional=0,
                time_mode="none",
                missing_funding_rate_per_8h=0.001,
            ),
            funding_frames={"A": pd.DataFrame(columns=["date", "rate"])},
        )
        summary = summarize_backtest(result)

        self.assertGreater(result.trades[0].funding, 0)
        self.assertAlmostEqual(result.trades[0].funding, result.trades[0].imputed_funding)
        self.assertAlmostEqual(summary["imputed_funding"], result.trades[0].imputed_funding)

    def test_drawdown_uses_adverse_intrabar_price_not_only_close(self) -> None:
        candles = {"A": frame([100.2, 100.2], [99.8, 95.0], [100, 100])}
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "long", 90, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(fee_rate=0, slippage_rate=0, min_notional=0),
        )

        self.assertGreater(summarize_backtest(result)["drawdown"], 0)

    def test_pyramid_adds_to_winner_and_stays_within_risk_cap(self) -> None:
        # Entry at 100, stop 99; bar1 reaches +1R open=101 and stays open for add.
        candles = {
            "A": pd.DataFrame(
                {
                    "date": pd.date_range("2025-01-01", periods=4, freq="15min", tz="UTC"),
                    "open": [100.0, 101.0, 101.2, 101.0],
                    "high": [100.2, 101.3, 101.5, 101.2],
                    "low": [99.8, 100.8, 100.9, 99.5],
                    "close": [100.0, 101.1, 101.3, 100.0],
                    "volume": 1.0,
                    "ema20": [100.0, 100.5, 100.8, 100.6],
                }
            )
        }
        event = EntryEvent(pd.Timestamp("2025-01-01", tz="UTC"), "A", "long", 99, 1)

        result = simulate_portfolio(
            candles,
            [event],
            BacktestOptions(
                fee_rate=0,
                slippage_rate=0,
                min_notional=0,
                pyramid_enabled=True,
                pyramid_trigger_r=1.0,
                pyramid_risk_fraction=0.5,
                max_pyramids=1,
                time_mode="none",
                exit_mode="fixed3",
            ),
        )

        self.assertEqual(len(result.trades), 1)
        # Average entry should move above 100 after the add at ~101.
        self.assertGreater(result.trades[0].entry_price, 100.0)
        self.assertLessEqual(summarize_backtest(result)["drawdown"], 0.35)


if __name__ == "__main__":
    unittest.main()
