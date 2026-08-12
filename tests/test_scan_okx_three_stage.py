from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pandas as pd

from scripts.scan_okx_three_stage import markdown_report, sort_results
from user_data.strategy_lib.six_ma_three_stage_screener import ThreeStageParameters


class ScanOkxThreeStageTests(unittest.TestCase):
    def test_sort_results_prioritizes_the_actionable_state_machine(self) -> None:
        results = pd.DataFrame(
            [
                row("WATCH-USDT-SWAP", "watch_both_coiled", 70),
                row("READY-USDT-SWAP", "ready_4h_breakout_15m_coiled", 80),
                row("WAIT-USDT-SWAP", "wait_first_pullback", 60),
                row("ENTRY-USDT-SWAP", "entry_confirmed", 50),
                row("NONE-USDT-SWAP", "none", 99),
            ]
        )

        ordered = sort_results(results)

        self.assertEqual(
            ordered["instrument"].tolist(),
            [
                "ENTRY-USDT-SWAP",
                "WAIT-USDT-SWAP",
                "READY-USDT-SWAP",
                "WATCH-USDT-SWAP",
                "NONE-USDT-SWAP",
            ],
        )
        self.assertNotIn("stage_order", ordered.columns)

    def test_markdown_report_has_four_tables_and_never_lists_references(self) -> None:
        results = pd.DataFrame(
            [
                row("ENTRY-USDT-SWAP", "entry_confirmed", 90),
                row("WAIT-USDT-SWAP", "wait_first_pullback", 80),
                row("READY-USDT-SWAP", "ready_4h_breakout_15m_coiled", 70),
                row("WATCH-USDT-SWAP", "watch_both_coiled", 60),
                {
                    **row("NEAR-USDT-SWAP", "none", 55),
                    "direction": "long",
                    "reason": "fifteen_minute_coil_not_ready",
                },
                row("BTC-USDT-SWAP", "entry_confirmed", 100, role="reference"),
            ]
        )

        report = markdown_report(
            results,
            datetime(2025, 1, 3, tzinfo=timezone.utc),
            ThreeStageParameters(),
            total_live=5,
            errors={},
        )

        headings = [
            "## 1. 回踩确认",
            "## 2. 等待第一次回踩",
            "## 3. 4H已突破、15m重新缠绕",
            "## 4. 4H与15m同时缠绕",
        ]
        positions = [report.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("BTC-USDT-SWAP", report)
        for instrument in ("ENTRY", "WAIT", "READY", "WATCH"):
            self.assertIn(f"{instrument}-USDT-SWAP", report)
        self.assertIn("## 接近下一阶段", report)
        self.assertIn("NEAR-USDT-SWAP", report)
        self.assertIn("不是自动开仓指令", report)


def row(
    instrument: str,
    stage: str,
    score: float,
    *,
    role: str = "trade",
) -> dict[str, object]:
    return {
        "instrument": instrument,
        "role": role,
        "stage": stage,
        "direction": "long" if stage not in {"watch_both_coiled", "none"} else "none",
        "daily_bias": "neutral",
        "four_hour_coil_score": score,
        "fifteen_minute_coil_score": score,
        "four_hour_zone_high": 101.0,
        "four_hour_zone_low": 99.0,
        "four_hour_breakout_at": pd.Timestamp("2025-01-03 04:00", tz="UTC"),
        "fifteen_minute_zone_high": 100.5,
        "fifteen_minute_zone_low": 99.5,
        "fifteen_minute_breakout_at": pd.Timestamp("2025-01-03 08:15", tz="UTC"),
        "first_pullback_at": pd.Timestamp("2025-01-03 08:30", tz="UTC"),
        "initial_stop_price": 99.1,
        "next_executable_at": pd.Timestamp("2025-01-03 08:30", tz="UTC"),
        "reason": "fixture",
    }


if __name__ == "__main__":
    unittest.main()
