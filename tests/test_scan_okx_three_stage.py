from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pandas as pd

from scripts.scan_okx_three_stage import (
    actionable_count,
    build_results,
    build_scanner_universe,
    build_manifest,
    markdown_report,
    observation_rows,
    sort_results,
    _window_diagnostics,
)
from user_data.strategy_lib.six_ma_three_stage_screener import ThreeStageParameters


class ScanOkxThreeStageTests(unittest.TestCase):
    def test_empty_universe_build_results_can_render_a_report(self) -> None:
        results = build_results(
            [],
            {},
            {},
            {},
            pd.Timestamp("2025-01-03 00:00", tz="UTC"),
            ThreeStageParameters(),
        )

        self.assertTrue(results.empty)
        manifest = build_manifest(
            results,
            datetime(2025, 1, 3, tzinfo=timezone.utc),
            ThreeStageParameters(),
            total_live=0,
            eligible_trade_count=0,
            scanner_exclusions={},
            errors={},
        )
        self.assertEqual(manifest["stageCounts"], {})
        self.assertEqual(manifest["observationCounts"]["included"], 0)
        report = markdown_report(
            results,
            datetime(2025, 1, 3, tzinfo=timezone.utc),
            ThreeStageParameters(),
            total_live=0,
            errors={},
        )

        self.assertIn("当前没有符合该阶段的合约", report)

    def test_stage_table_treats_optional_lifecycle_columns_as_missing(self) -> None:
        results = pd.DataFrame(
            [
                {
                    "instrument": "MINIMAL-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "direction": "long",
                    "daily_bias": "neutral",
                    "fifteen_minute_state": "breakout_long",
                    "fifteen_minute_coil_score": 10.0,
                    "fifteen_minute_contraction_ratio": 0.2,
                    "fifteen_minute_current_compression_atr": 1.0,
                    "four_hour_context": "unknown",
                    "four_hour_context_score": 0.0,
                    "risk_labels": [],
                    "initial_stop_price": 99.0,
                    "next_executable_at": pd.NaT,
                    "reason": "active_setup",
                }
            ]
        )

        report = markdown_report(
            results,
            datetime(2025, 1, 3, tzinfo=timezone.utc),
            ThreeStageParameters(),
            total_live=1,
            errors={},
        )

        self.assertIn("MINIMAL-USDT-SWAP", report)
        self.assertIn("active_setup", report)

    def test_actionable_count_includes_formal_and_new_observation_stages(self) -> None:
        results = pd.DataFrame(
            {
                "stage": [
                    "entry_confirmed",
                    "wait_first_pullback",
                    "mature_15m_coil",
                    "forming_15m_coil",
                    "none",
                ]
            }
        )

        self.assertEqual(actionable_count(results), 4)

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

    def test_sort_results_puts_formal_stages_before_mature_then_forming_observations(
        self,
    ) -> None:
        results = pd.DataFrame(
            [
                {**row("FORMING-USDT-SWAP", "forming_15m_coil", 95), "four_hour_context_score": 99},
                {**row("MATURE-LOW-USDT-SWAP", "mature_15m_coil", 20), "four_hour_context_score": 10},
                {**row("WAIT-USDT-SWAP", "wait_first_pullback", 1), "four_hour_context_score": 0},
                {**row("ENTRY-USDT-SWAP", "entry_confirmed", 1), "four_hour_context_score": 0},
                {**row("MATURE-HIGH-USDT-SWAP", "mature_15m_coil", 80), "four_hour_context_score": 40},
            ]
        )

        ordered = sort_results(results)

        self.assertEqual(
            ordered["instrument"].tolist(),
            [
                "ENTRY-USDT-SWAP",
                "WAIT-USDT-SWAP",
                "MATURE-HIGH-USDT-SWAP",
                "MATURE-LOW-USDT-SWAP",
                "FORMING-USDT-SWAP",
            ],
        )

    def test_observation_rows_are_positive_score_and_capped_at_twenty(self) -> None:
        rows = [
            row(f"MATURE-{index:02d}-USDT-SWAP", "mature_15m_coil", 100 - index)
            for index in range(12)
        ]
        rows.extend(
            row(f"FORMING-{index:02d}-USDT-SWAP", "forming_15m_coil", 100 - index)
            for index in range(13)
        )
        rows.append(row("ZERO-USDT-SWAP", "mature_15m_coil", 0))

        selected = observation_rows(pd.DataFrame(rows), ThreeStageParameters())

        self.assertEqual(len(selected), 20)
        self.assertNotIn("ZERO-USDT-SWAP", selected["instrument"].tolist())
        self.assertTrue((selected["fifteen_minute_coil_score"] > 0).all())
        self.assertEqual(
            set(selected["stage"]), {"mature_15m_coil", "forming_15m_coil"}
        )
        self.assertLess(
            selected.index[selected["stage"] == "mature_15m_coil"].max(),
            selected.index[selected["stage"] == "forming_15m_coil"].min(),
        )

    def test_markdown_report_includes_btc_eth_trade_rows_with_reference_marker(self) -> None:
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
                {
                    **row("BTC-USDT-SWAP", "entry_confirmed", 100),
                    "market_reference": True,
                    "bitget_available": True,
                    "scanner_eligible": True,
                },
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
            "## 3. 15m成熟缠绕观察",
            "## 4. 15m正在收拢观察",
        ]
        positions = [report.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("BTC-USDT-SWAP", report)
        for instrument in ("ENTRY", "WAIT", "READY", "WATCH"):
            self.assertIn(f"{instrument}-USDT-SWAP", report)
        self.assertNotIn("## 接近下一阶段", report)
        self.assertNotIn("NEAR-USDT-SWAP", report)
        self.assertIn("不是自动开仓指令", report)
        self.assertIn("15m状态/分数", report)
        self.assertIn("max_observation_items", report)
        self.assertIn("观察层合计纳入", report)

    def test_markdown_report_names_layered_observations_and_exposes_window_fields(
        self,
    ) -> None:
        results = pd.DataFrame(
            [
                {
                    **row("MATURE-USDT-SWAP", "mature_15m_coil", 70),
                    "bitget_available": True,
                    "fifteen_minute_window_end": pd.Timestamp(
                        "2025-01-03 08:15", tz="UTC"
                    ),
                    "four_hour_window_end": pd.Timestamp(
                        "2025-01-03 04:00", tz="UTC"
                    ),
                    "current_close_vs_six_lines": "above_all",
                }
            ]
        )

        report = markdown_report(
            results,
            datetime(2025, 1, 3, tzinfo=timezone.utc),
            ThreeStageParameters(),
            total_live=1,
            errors={},
        )

        self.assertIn("## 3. 15m成熟缠绕观察", report)
        self.assertIn("## 4. 15m正在收拢观察", report)
        self.assertIn("15m-first", report)
        self.assertIn("高周期上下文", report)
        self.assertIn("Bitget可用", report)
        self.assertIn("15m窗口末端", report)
        self.assertIn("4H窗口末端", report)
        self.assertIn("当前收盘相对六线", report)
        self.assertIn("above_all", report)
        self.assertIn("2025-01-03T08:15:00+00:00", report)
        self.assertIn("2025-01-03T04:00:00+00:00", report)

    def test_scanner_universe_is_crypto_only_stablecoin_safe_and_bitget_intersected(self) -> None:
        rows = [
            universe_row("BTC-USDT-SWAP", "1"),
            universe_row("ETH-USDT-SWAP", "1"),
            universe_row("ALT-USDT-SWAP", "1"),
            universe_row("USDC-USDT-SWAP", "1"),
            universe_row("MU-USDT-SWAP", "3"),
            universe_row("OFC-USDT-SWAP", "1"),
        ]

        universe, exclusions = build_scanner_universe(
            rows,
            {"BTCUSDT", "ETHUSDT", "ALTUSDT", "MUUSDT"},
        )
        by_id = {row["instId"]: row for row in universe}

        self.assertEqual(
            [row["instId"] for row in universe if row["scanner_eligible"]],
            ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "ALT-USDT-SWAP"],
        )
        self.assertEqual(by_id["BTC-USDT-SWAP"]["role"], "trade")
        self.assertTrue(by_id["BTC-USDT-SWAP"]["market_reference"])
        self.assertFalse(by_id["ALT-USDT-SWAP"]["market_reference"])
        self.assertEqual(by_id["USDC-USDT-SWAP"]["scanner_exclusion_reason"], "stablecoin_base")
        self.assertEqual(by_id["MU-USDT-SWAP"]["scanner_exclusion_reason"], "non_crypto_category")
        self.assertEqual(by_id["OFC-USDT-SWAP"]["scanner_exclusion_reason"], "bitget_unavailable")
        self.assertEqual(exclusions["stablecoin_base"], 1)
        self.assertEqual(exclusions["non_crypto_category"], 1)
        self.assertEqual(exclusions["bitget_unavailable"], 1)

    def test_common_stablecoin_bases_are_excluded_even_with_bitget_contracts(self) -> None:
        bases = ("BUSD", "EURC", "EURT", "USDB", "USD0", "GHO", "CRVUSD")
        rows = [universe_row(f"{base}-USDT-SWAP", "1") for base in bases]
        universe, exclusions = build_scanner_universe(
            rows,
            {f"{base}USDT" for base in bases},
        )

        self.assertEqual(
            {row["instId"] for row in universe if row["scanner_eligible"]},
            set(),
        )
        self.assertEqual(exclusions["stablecoin_base"], len(bases))

    def test_manifest_records_scanner_universe_and_exclusion_counts(self) -> None:
        results = pd.DataFrame([row("BTC-USDT-SWAP", "none", 0)])
        manifest = build_manifest(
            results,
            datetime(2025, 1, 3, tzinfo=timezone.utc),
            ThreeStageParameters(),
            total_live=6,
            eligible_trade_count=3,
            scanner_exclusions={"stablecoin_base": 1, "bitget_unavailable": 2},
            errors={},
        )

        self.assertEqual(manifest["eligibleTradeContracts"], 3)
        self.assertEqual(manifest["scannerExclusionCounts"]["stablecoin_base"], 1)
        self.assertEqual(manifest["scannerUniverse"]["instCategory"], "1")
        self.assertIn("USDC", manifest["scannerUniverse"]["stablecoinBasesExcluded"])
        self.assertTrue(manifest["scannerUniverse"]["bitgetContractIntersection"])
        self.assertEqual(manifest["minAgeDays"], 0)
        self.assertEqual(manifest["observationCounts"]["mature_15m_coil"], 0)
        self.assertEqual(manifest["observationCounts"]["forming_15m_coil"], 0)

    def test_manifest_stage_counts_only_include_eligible_trade_rows(self) -> None:
        results = pd.DataFrame(
            [
                {
                    **row("ELIGIBLE-USDT-SWAP", "mature_15m_coil", 70),
                    "scanner_eligible": True,
                },
                {
                    **row("EXCLUDED-USDT-SWAP", "none", 0),
                    "scanner_eligible": False,
                    "scanner_exclusion_reason": "stablecoin_base",
                },
            ]
        )

        manifest = build_manifest(
            results,
            datetime(2025, 1, 3, tzinfo=timezone.utc),
            ThreeStageParameters(),
            total_live=2,
            eligible_trade_count=1,
            scanner_exclusions={"stablecoin_base": 1},
            errors={},
        )

        self.assertEqual(manifest["stageCounts"], {"mature_15m_coil": 1})
        self.assertNotIn("none", manifest["stageCounts"])
        self.assertEqual(manifest["scannerExclusionCounts"]["stablecoin_base"], 1)

    def test_window_diagnostics_compute_completed_24h_percent_change(self) -> None:
        dates = pd.date_range("2025-01-01", periods=100, freq="15min", tz="UTC")
        closes = [100.0 if index <= 3 else 110.0 for index in range(len(dates))]
        frame = pd.DataFrame(
            {
                "date": dates,
                "open": closes,
                "high": [value + 0.2 for value in closes],
                "low": [value - 0.2 for value in closes],
                "close": closes,
                "volume": [1.0] * len(dates),
            }
        )

        diagnostic = _window_diagnostics(
            frame,
            pd.Timestamp("2025-01-02 01:00", tz="UTC"),
            pd.Timedelta(minutes=15),
        )

        self.assertEqual(diagnostic["current_close"], 110.0)
        self.assertAlmostEqual(float(diagnostic["change_24h_pct"]), 10.0, places=6)


def universe_row(inst_id: str, category: str) -> dict[str, object]:
    return {
        "instId": inst_id,
        "instCategory": category,
        "state": "live",
        "eligible": True,
        "eligibility_reason": "eligible",
        "role": "reference" if inst_id.split("-")[0] in {"BTC", "ETH"} else "trade",
    }


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
        "four_hour_context": "unknown",
        "four_hour_context_score": 0.0,
        "risk_labels": [],
        "fifteen_minute_current_compression_atr": 1.0,
        "fifteen_minute_compressed_fraction": 0.75,
        "fifteen_minute_trailing_compressed_bars": 4,
        "fifteen_minute_contraction_ratio": 0.25,
    }


if __name__ == "__main__":
    unittest.main()
