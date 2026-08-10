from __future__ import annotations

import unittest

import pandas as pd

from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
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


if __name__ == "__main__":
    unittest.main()
