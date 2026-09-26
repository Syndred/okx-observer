from __future__ import annotations

from dataclasses import replace
import unittest

import pandas as pd

from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
    summarize_backtest,
)


def candles(count: int = 4) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=count, freq="5min", tz="UTC"),
        "open": [100.0] * count,
        "high": [100.2] * count,
        "low": [99.8] * count,
        "close": [100.0] * count,
        "volume": [1.0] * count,
    })


BASE = BacktestOptions(
    exit_mode="fixed3", time_mode="none", take_profit_r=0.5,
    max_hold_minutes=15, candle_minutes=5, fee_rate=0, slippage_rate=0,
    missing_funding_rate_per_8h=0,
)


def run(source: pd.DataFrame, side: str = "long", options: BacktestOptions = BASE):
    event = EntryEvent(source.iloc[0]["date"], "A", side, 99 if side == "long" else 101, 1)
    return simulate_portfolio({"A": source}, [event], options)


class ScalpBacktesterTests(unittest.TestCase):
    def test_fractional_target_and_stop_are_symmetric(self) -> None:
        for side, price in (("long", 100.5), ("short", 99.5)):
            for mode in ("fixed3", "fixed5"):
                with self.subTest(side=side, mode=mode):
                    source = candles()
                    source.loc[0, "high" if side == "long" else "low"] = price
                    trade = run(source, side, replace(BASE, exit_mode=mode)).trades[0]
                    self.assertEqual(trade.exit_reason, "fixed_0.5r")
                    self.assertEqual(trade.exit_price, price)
                    self.assertAlmostEqual(trade.r_multiple, 0.5)

    def test_same_bar_stop_precedes_fractional_target_and_timeout(self) -> None:
        for side in ("long", "short"):
            with self.subTest(side=side):
                source = candles()
                source.loc[0, ["high", "low"]] = [101.5, 98.5]
                result = run(source, side, replace(BASE, max_hold_minutes=5))
                self.assertEqual(result.trades[0].exit_reason, "initial_stop")
                self.assertLess(result.trades[0].net_pnl, 0)

    def test_target_precedes_timeout(self) -> None:
        source = candles()
        source.loc[2, "high"] = 100.5
        self.assertEqual(run(source).trades[0].exit_reason, "fixed_0.5r")

    def test_fifteen_minute_exit_uses_third_five_minute_close(self) -> None:
        for side in ("long", "short"):
            with self.subTest(side=side):
                source = candles()
                source.loc[2, "close"] = 100.1
                source.loc[3, "close"] = 99.9
                result = run(source, side)
                trade = result.trades[0]
                self.assertEqual(trade.exit_reason, "max_hold_15m")
                self.assertEqual(trade.exit_price, 100.1)
                self.assertEqual(trade.close_date, source.iloc[0]["date"] + pd.Timedelta(minutes=15))
                self.assertAlmostEqual(summarize_backtest(result)["average_holding_hours"], 0.25)
                self.assertEqual(list(result.equity_curve["open_positions"]), [1, 1, 0, 0])

    def test_costs_are_deducted_and_small_target_can_be_net_loss(self) -> None:
        source = candles()
        source.loc[0, "high"] = 100.8
        profitable = run(source, options=replace(BASE, fee_rate=0.0005, slippage_rate=0.0005)).trades[0]
        self.assertEqual(profitable.exit_reason, "fixed_0.5r")
        self.assertGreater(profitable.net_pnl, 0)
        self.assertLess(profitable.r_multiple, 0.5)
        self.assertGreater(profitable.fees, 0)
        loss = run(source, options=replace(BASE, take_profit_r=0.05, fee_rate=0.0005, slippage_rate=0.0005))
        self.assertEqual(loss.trades[0].exit_reason, "fixed_0.05r")
        self.assertLess(loss.trades[0].net_pnl, 0)
        self.assertEqual(summarize_backtest(loss)["win_rate"], 0)

    def test_invalid_options_are_rejected(self) -> None:
        variants = [
            {"take_profit_r": value} for value in (0, -1, float("inf"), float("nan"), True, "0.5")
        ] + [
            {"candle_minutes": value} for value in (0, -5, 5.0, float("inf"), float("nan"), True)
        ] + [
            {"max_hold_minutes": value} for value in (0, -5, 12, 15.0, float("inf"), float("nan"), True)
        ] + [
            {"exit_mode": mode} for mode in ("hybrid", "margin", "streak_flip")
        ]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                simulate_portfolio({}, [], replace(BASE, **changes))

    def test_timeout_rejects_other_modes_without_target_override(self) -> None:
        for mode in ("hybrid", "margin"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "max_hold_minutes requires"):
                run(candles(), options=replace(BASE, take_profit_r=None, exit_mode=mode))


if __name__ == "__main__":
    unittest.main()
