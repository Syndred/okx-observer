from __future__ import annotations

import unittest
from unittest.mock import Mock

import pandas as pd

from user_data.strategies.OKX_Shortline_V2 import (
    OKX_Shortline_V2,
    encode_v2_entry_tag,
    parse_v2_entry_tag,
)


class OkxV2StrategyTests(unittest.TestCase):
    def test_entry_tag_round_trip_preserves_immutable_stop_and_risk(self) -> None:
        tag = encode_v2_entry_tag("short", 101.23456789, 0.0075)

        self.assertEqual(parse_v2_entry_tag(tag), ("short", 101.23456789, 0.0075))

    def test_leverage_is_three_and_respects_exchange_limit(self) -> None:
        strategy = OKX_Shortline_V2(config={})
        now = pd.Timestamp("2025-01-01", tz="UTC").to_pydatetime()

        self.assertEqual(
            strategy.leverage("XRP/USDT:USDT", now, 1, 1, 2, None, "long"),
            2,
        )

    def test_stake_never_rounds_up_to_exchange_minimum(self) -> None:
        strategy = OKX_Shortline_V2(config={})
        strategy.wallets = Mock()
        strategy.wallets.get_total_stake_amount.return_value = 100.0
        strategy._portfolio_snapshot = Mock(return_value=([], [], 0.0))
        now = pd.Timestamp("2025-01-01", tz="UTC").to_pydatetime()

        stake = strategy.custom_stake_amount(
            pair="XRP/USDT:USDT",
            current_time=now,
            current_rate=100.0,
            proposed_stake=10.0,
            min_stake=10.0,
            max_stake=100.0,
            leverage=3.0,
            entry_tag=encode_v2_entry_tag("long", 95.0, 0.0075),
            side="long",
        )

        self.assertEqual(stake, 0.0)

    def test_stake_uses_stop_distance_and_available_risk_budget(self) -> None:
        strategy = OKX_Shortline_V2(config={})
        strategy.wallets = Mock()
        strategy.wallets.get_total_stake_amount.return_value = 100.0
        strategy._portfolio_snapshot = Mock(return_value=([], [], 0.0))
        now = pd.Timestamp("2025-01-01", tz="UTC").to_pydatetime()

        stake = strategy.custom_stake_amount(
            pair="XRP/USDT:USDT",
            current_time=now,
            current_rate=100.0,
            proposed_stake=10.0,
            min_stake=1.0,
            max_stake=100.0,
            leverage=3.0,
            entry_tag=encode_v2_entry_tag("long", 99.0, 0.0075),
            side="long",
        )

        self.assertAlmostEqual(stake, 25.0)


if __name__ == "__main__":
    unittest.main()
