from __future__ import annotations

import unittest
import base64
import importlib.util
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pandas as pd

from freqtrade.persistence import Trade
from user_data.strategies.MA_EMA_Trend_Strategy import MA_EMA_Trend_Strategy


class FreqtradeStrategyTests(unittest.TestCase):
    def test_strategy_dependencies_can_unpickle_in_hyperopt_worker(self) -> None:
        strategy_dir = Path("/freqtrade/user_data/strategies")
        sys.path.insert(0, str(strategy_dir))
        try:
            spec = importlib.util.spec_from_file_location(
                "resolver_loaded_strategy", strategy_dir / "MA_EMA_Trend_Strategy.py"
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            payload = base64.b64encode(module.collateral_for_risk.__module__.encode()).decode()
        finally:
            sys.path.remove(str(strategy_dir))

        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import base64,importlib,sys; "
                "name=base64.b64decode(sys.argv[1]).decode(); importlib.import_module(name)",
                payload,
            ],
            cwd="/freqtrade",
            capture_output=True,
            text=True,
        )

        self.assertEqual(child.returncode, 0, child.stderr)

    def test_populates_long_and_short_first_pullback_signals(self) -> None:
        strategy = MA_EMA_Trend_Strategy(config={})
        closes = [100.0] * 125 + [101.0, 100.5, 100.0, 99.0, 99.5]
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=len(closes), freq="1h", tz="UTC"),
                "open": closes,
                "high": [price + 0.2 for price in closes],
                "low": [price - 0.2 for price in closes],
                "close": closes,
                "volume": 1.0,
            }
        )
        frame.loc[126, "low"] = 100.1
        frame.loc[129, "high"] = 99.9

        indicators = strategy.populate_indicators(frame, {"pair": "TEST/USDT:USDT"})
        result = strategy.populate_entry_trend(indicators, {"pair": "TEST/USDT:USDT"})

        self.assertEqual(result.loc[126, "enter_long"], 1)
        self.assertTrue(result.loc[126, "enter_tag"].startswith("v1_long|stop="))
        self.assertEqual(result.loc[129, "enter_short"], 1)
        self.assertTrue(result.loc[129, "enter_tag"].startswith("v1_short|stop="))

    def test_leverage_uses_configured_value_but_respects_exchange_limit(self) -> None:
        strategy = MA_EMA_Trend_Strategy(config={})
        strategy.buy_leverage.value = 10

        leverage = strategy.leverage(
            pair="TEST/USDT:USDT",
            current_time=pd.Timestamp("2025-01-01", tz="UTC").to_pydatetime(),
            current_rate=100.0,
            proposed_leverage=1.0,
            max_leverage=7.0,
            entry_tag="v1_long|stop=99.0000000000",
            side="long",
        )

        self.assertEqual(leverage, 7.0)

    def test_matrix_environment_overrides_leverage_and_risk(self) -> None:
        strategy = MA_EMA_Trend_Strategy(config={})

        with patch.dict(
            "os.environ",
            {"V1_LEVERAGE": "3", "V1_RISK_PCT": "0.02"},
            clear=False,
        ):
            self.assertEqual(strategy.configured_leverage(), 3.0)
            self.assertEqual(strategy.configured_risk_pct(), 0.02)

    def test_stop_and_roi_are_derived_from_entry_cluster_risk(self) -> None:
        strategy = MA_EMA_Trend_Strategy(config={})
        strategy.sell_reward_risk.value = 5
        now = pd.Timestamp("2025-01-01", tz="UTC").to_pydatetime()
        trade = Trade(
            pair="TEST/USDT:USDT",
            open_rate=100.0,
            stake_amount=20.0,
            amount=1.0,
            fee_open=0.0005,
            fee_close=0.0005,
            is_open=True,
            open_date=now,
            exchange="binance",
            strategy="MA_EMA_Trend_Strategy",
            timeframe=60,
            trading_mode="futures",
            leverage=5.0,
            is_short=False,
            enter_tag="v1_long|stop=99.0000000000",
        )

        stop_distance = strategy.custom_stoploss(
            pair=trade.pair,
            trade=trade,
            current_time=now,
            current_rate=100.0,
            current_profit=0.0,
            after_fill=True,
        )
        roi = strategy.custom_roi(
            pair=trade.pair,
            trade=trade,
            current_time=now,
            trade_duration=0,
            entry_tag=trade.enter_tag,
            side="long",
        )

        self.assertAlmostEqual(stop_distance, 0.05)
        self.assertAlmostEqual(roi, 0.25)


if __name__ == "__main__":
    unittest.main()
