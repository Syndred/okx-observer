from __future__ import annotations

import unittest

import pandas as pd

from user_data.strategy_lib.v2_backtester import (
    BacktestOptions,
    EntryEvent,
    simulate_portfolio,
)


UTC = "UTC"
START = pd.Timestamp("2025-01-01 00:00", tz=UTC)


def _frame(
    *,
    side: str,
    leverage: float,
    margin_roi: float,
    rows: int = 2,
    same_candle_stop: bool = False,
) -> pd.DataFrame:
    entry = 100.0
    stop = entry - 0.1 if side == "long" else entry + 0.1
    target = (
        entry * (1 + margin_roi / leverage)
        if side == "long"
        else entry * (1 - margin_roi / leverage)
    )
    dates = pd.date_range(START, periods=rows, freq="15min", tz=UTC)
    opens = [entry] * rows
    highs = [entry + 0.05] * rows
    lows = [entry - 0.05] * rows
    if side == "long":
        highs[-1] = target + 0.05
        lows[-1] = stop - 0.05 if same_candle_stop else stop + 0.01
    else:
        lows[-1] = target - 0.05
        highs[-1] = stop + 0.05 if same_candle_stop else stop - 0.01
    closes = [entry] * (rows - 1) + [target if not same_candle_stop else entry]
    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1.0,
            "ema20": closes,
        }
    )


def _options(
    *,
    fraction: float,
    leverage: float,
    margin_roi: float = 0.10,
    **kwargs: object,
) -> BacktestOptions:
    return BacktestOptions(
        initial_equity=1_000.0,
        leverage=leverage,
        collateral_fraction=fraction,
        margin_take_profit=margin_roi,
        exit_mode="margin",
        time_mode="72h",
        fee_rate=0.0,
        slippage_rate=0.0,
        min_notional=0.0,
        **kwargs,
    )


class MarginBacktesterTests(unittest.TestCase):
    def test_margin_target_is_an_roi_on_allocated_margin_for_both_sides(self) -> None:
        for side, stop in (("long", 99.9), ("short", 100.1)):
            with self.subTest(side=side):
                pair = f"{side.upper()}-USDT-SWAP"
                result = simulate_portfolio(
                    {pair: _frame(side=side, leverage=3.0, margin_roi=0.10)},
                    [EntryEvent(START, pair, side, stop, 1)],
                    _options(fraction=0.30, leverage=3.0),
                )

                self.assertEqual(len(result.trades), 1)
                trade = result.trades[0]
                self.assertEqual(trade.exit_reason, "margin_0.1")
                # 30% of 1,000 collateral x 3 leverage = 900 notional;
                # 10% margin ROI therefore realizes 30 USDT before fees.
                self.assertAlmostEqual(trade.net_pnl, 30.0, places=8)
                self.assertAlmostEqual(result.final_equity, 1_030.0, places=8)

    def test_margin_fraction_and_leverage_scale_target_profit(self) -> None:
        cases = ((0.30, 3.0, 30.0), (0.40, 5.0, 40.0))
        for fraction, leverage, expected_pnl in cases:
            with self.subTest(fraction=fraction, leverage=leverage):
                pair = f"CASE-{int(fraction * 100)}-{int(leverage)}"
                side = "long"
                result = simulate_portfolio(
                    {pair: _frame(side=side, leverage=leverage, margin_roi=0.10)},
                    [EntryEvent(START, pair, side, 99.9, 1)],
                    _options(fraction=fraction, leverage=leverage),
                )

                self.assertAlmostEqual(result.trades[0].net_pnl, expected_pnl, places=8)

    def test_same_candle_stop_has_priority_over_margin_target(self) -> None:
        pair = "SAME-CANDLE-USDT-SWAP"
        result = simulate_portfolio(
            {pair: _frame(side="long", leverage=3.0, margin_roi=0.10, same_candle_stop=True)},
            [EntryEvent(START, pair, "long", 99.9, 1)],
            _options(fraction=0.30, leverage=3.0),
        )

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_reason, "initial_stop")
        self.assertLess(result.trades[0].net_pnl, 0.0)
        self.assertAlmostEqual(result.final_equity, 999.1, places=8)

    def test_second_trade_sizes_from_post_close_marked_equity(self) -> None:
        dates = pd.date_range(START, periods=4, freq="15min", tz=UTC)
        target = 100.0 * (1 + 0.10 / 3.0)
        def rows(target_index: int) -> pd.DataFrame:
            highs = [100.05] * 4
            lows = [99.95] * 4
            closes = [100.0] * 4
            highs[target_index] = target + 0.05
            closes[target_index] = target
            return pd.DataFrame(
                {
                    "date": dates,
                    "open": [100.0] * 4,
                    "high": highs,
                    "low": lows,
                    "close": closes,
                    "volume": 1.0,
                    "ema20": closes,
                }
            )

        frames = {"A-USDT-SWAP": rows(1), "B-USDT-SWAP": rows(3)}
        events = [
            EntryEvent(START, "A-USDT-SWAP", "long", 99.9, 1),
            EntryEvent(START + pd.Timedelta(minutes=30), "B-USDT-SWAP", "long", 99.9, 1),
        ]
        result = simulate_portfolio(
            frames,
            events,
            _options(fraction=0.30, leverage=3.0),
        )

        self.assertEqual(len(result.trades), 2)
        first, second = result.trades
        self.assertLess(first.close_date, second.open_date)
        self.assertAlmostEqual(first.net_pnl, 30.0, places=8)
        self.assertAlmostEqual(second.net_pnl, 30.9, places=8)
        self.assertAlmostEqual(second.initial_risk / first.initial_risk, 1.03, places=8)

    def test_margin_mode_does_not_add_to_an_existing_position(self) -> None:
        pair = "NO-PYRAMID-USDT-SWAP"
        dates = pd.date_range(START, periods=3, freq="15min", tz=UTC)
        frame = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0, 100.0, 100.0],
                "high": [100.05, 100.05, 103.5],
                "low": [99.95, 99.95, 99.95],
                "close": [100.0, 100.0, 103.333333],
                "volume": 1.0,
                "ema20": [100.0] * 3,
            }
        )
        result = simulate_portfolio(
            {pair: frame},
            [
                EntryEvent(START, pair, "long", 99.9, 1),
                EntryEvent(START + pd.Timedelta(minutes=15), pair, "long", 99.9, 1),
            ],
            _options(fraction=0.30, leverage=3.0),
        )

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.skipped_entries, 1)

    def test_single_trade_worst_loss_is_capped_at_ten_percent_of_account(self) -> None:
        pair = "RISK-CAP-USDT-SWAP"
        frame = pd.DataFrame(
            {
                "date": pd.date_range(START, periods=2, freq="15min", tz=UTC),
                "open": [100.0, 100.0],
                "high": [100.05, 100.05],
                "low": [99.95, 89.0],
                "close": [100.0, 90.0],
                "volume": 1.0,
                "ema20": [100.0, 100.0],
            }
        )
        result = simulate_portfolio(
            {pair: frame},
            [EntryEvent(START, pair, "long", 90.0, 1)],
            _options(
                fraction=1.0,
                leverage=3.0,
                normal_trade_risk=0.10,
                reduced_trade_risk=0.10,
                max_portfolio_risk=0.10,
            ),
        )

        trade = result.trades[0]
        self.assertLessEqual(trade.initial_risk, 100.0 + 1e-8)
        self.assertGreaterEqual(result.final_equity, 900.0 - 1e-8)

    def test_margin_options_validate_fraction_and_target(self) -> None:
        pair = "VALIDATE-USDT-SWAP"
        frame = _frame(side="long", leverage=3.0, margin_roi=0.10)
        event = EntryEvent(START, pair, "long", 99.9, 1)
        with self.assertRaises(ValueError):
            simulate_portfolio(
                {pair: frame},
                [event],
                _options(fraction=0.0, leverage=3.0),
            )
        with self.assertRaises(ValueError):
            simulate_portfolio(
                {pair: frame},
                [event],
                _options(fraction=0.30, leverage=3.0, margin_roi=0.0),
            )


if __name__ == "__main__":
    unittest.main()
