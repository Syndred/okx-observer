from __future__ import annotations

import unittest

import pandas as pd

from scripts.report_okx_v2 import (
    frozen_strategy_lines,
    market_filter_description,
    threshold_crossings,
)


class ReportOkxV2Tests(unittest.TestCase):
    def test_threshold_crossings_return_first_date_or_never(self) -> None:
        curve = pd.DataFrame(
            {
                "date": pd.date_range("2025-01-01", periods=3, freq="1h", tz="UTC"),
                "equity": [100.0, 1001.0, 900.0],
            }
        )

        result = threshold_crossings(curve, (1_000, 10_000))

        self.assertEqual(result[1_000], "2025-01-01T01:00:00+00:00")
        self.assertEqual(result[10_000], "never")

    def test_six_ma_report_renders_trend_and_market_filter_parameters(self) -> None:
        frozen = {
            "signal_mode": "six_ma_mtf",
            "parameters": {
                "entry_trigger": "nested_breakout",
                "require_full_4h_trend": False,
                "compression_4h_atr": 2.0,
                "breakout_4h_atr": 0.05,
                "compression_15m_atr": 2.0,
                "breakout_15m_atr": 0.05,
                "zone_max_age_4h": 12,
                "setup_wait_15m": 48,
            },
        }

        rendered = "\n".join(frozen_strategy_lines(frozen))

        self.assertIn("不额外要求完整均线排列", rendered)
        self.assertEqual(
            market_filter_description({"strict_market_consensus": False}),
            "BTC/ETH 至少一个同向且两者均不反向",
        )
        self.assertEqual(
            market_filter_description({"strict_market_consensus": True}),
            "BTC 与 ETH 必须同时同向",
        )


if __name__ == "__main__":
    unittest.main()
