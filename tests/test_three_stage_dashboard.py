from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import pandas as pd
import yaml
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.scan_manager import (
    ScanManager,
    clean_log_line,
    report_artifact_lock,
    run_production_scan,
)
from dashboard.view_model import load_dashboard
from dashboard.view_model import bitget_url, format_shanghai, FEATURED_CAP, MOVERS_CAP, MOMENTUM_CAP


class DashboardViewModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self._tempdir.name)
        self._csv_path = self.temp_dir / "latest.csv"
        self._manifest_path = self.temp_dir / "run-manifest.json"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_bitget_url_maps_okx_swap_to_usdt_contract(self) -> None:
        self.assertEqual(
            bitget_url("LINK-USDT-SWAP"),
            "https://www.bitget.com/zh-CN/futures/usdt/LINKUSDT",
        )

    def test_format_shanghai_converts_utc_without_using_host_timezone(self) -> None:
        self.assertEqual(
            format_shanghai("2026-08-12T00:30:00+00:00"),
            "2026-08-12 08:30",
        )

    def test_cards_derive_bounded_readiness_and_chinese_display_fields(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "WAIT-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "wait_first_pullback",
                    "direction": "long",
                    "daily_bias": "short",
                    "four_hour_context": "opposite",
                    "four_hour_context_score": 0,
                    "risk_labels": ["opposite", "expanded_risk"],
                    "fifteen_minute_coil_score": 80,
                    "reason": "fifteen_minute_breakout_waiting_pullback",
                },
                {
                    "instrument": "ENTRY-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "entry_confirmed",
                    "direction": "long",
                    "daily_bias": "short",
                    "four_hour_context": "opposite",
                    "four_hour_context_score": 0,
                    "risk_labels": ["opposite", "expanded_risk"],
                    "fifteen_minute_coil_score": 80,
                    "reason": "first_pullback_held",
                },
                {
                    "instrument": "MAX-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "entry_confirmed",
                    "direction": "neutral",
                    "daily_bias": "unknown",
                    "four_hour_context": "unknown",
                    "four_hour_context_score": 1000,
                    "risk_labels": [],
                    "fifteen_minute_coil_score": 1000,
                    "reason": "first_pullback_held",
                },
            ],
        )

        result = load_dashboard(self.temp_dir)
        cards = {
            card["instrument"]: card
            for stage in result["stages"].values()
            for card in stage
        }

        wait = cards["WAIT-USDT-SWAP"]
        entry = cards["ENTRY-USDT-SWAP"]
        self.assertEqual(wait["entry_readiness"], 36)
        self.assertEqual(wait["entry_readiness_label"], "风险偏高")
        self.assertGreater(entry["entry_readiness"], wait["entry_readiness"])
        self.assertTrue(
            all(0 <= card["entry_readiness"] <= 100 for card in cards.values())
        )
        self.assertEqual(wait["stage_label"], "等待首次回踩")
        self.assertEqual(wait["direction_label"], "做多")
        self.assertEqual(wait["four_hour_context_label"], "高周期反向")
        self.assertEqual(wait["daily_bias_label"], "做空")
        self.assertEqual(
            wait["risk_labels_display"], ["均线已经发散"]
        )
        for field in (
            "stage_label",
            "direction_label",
            "four_hour_context_label",
            "daily_bias_label",
            "risk_labels_display",
        ):
            value = wait[field]
            values = value if isinstance(value, list) else [value]
            for item in values:
                self.assertNotIn(
                    item,
                    {
                        "entry_confirmed",
                        "wait_first_pullback",
                        "long",
                        "short",
                        "opposite",
                        "expanded_risk",
                    },
                )

    def test_latest_price_and_countertrend_observation_use_explicit_four_hour_inputs(
        self,
    ) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "BOTTOM-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "mature_15m_coil",
                    "direction": "short",
                    "four_hour_context": "opposite",
                    "four_hour_context_direction": "long",
                    "current_close": 0.00123456,
                    "current_close_vs_six_lines": "below_all",
                    "risk_labels": ["opposite", "expanded_risk"],
                    "reason": "fifteen_minute_coil_ready",
                },
                {
                    "instrument": "TOP-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "forming_15m_coil",
                    "direction": "long",
                    "four_hour_context": "trend_aligned",
                    "four_hour_context_direction": "short",
                    "current_close": 123.45,
                    "current_close_vs_six_lines": "above_all",
                    "risk_labels": [],
                    "reason": "fifteen_minute_coil_forming",
                },
                {
                    "instrument": "NO-SIGNAL-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "mature_15m_coil",
                    "direction": "long",
                    "four_hour_context": "unknown",
                    "four_hour_context_direction": "unknown",
                    "current_close": 99.0,
                    "current_close_vs_six_lines": "above_all",
                    "risk_labels": [],
                    "reason": "fifteen_minute_coil_ready",
                },
                {
                    "instrument": "INSIDE-BAND-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "mature_15m_coil",
                    "direction": "long",
                    "daily_bias": "short",
                    "four_hour_context_direction": "long",
                    "current_close": 100.0,
                    "current_close_vs_six_lines": "inside_six_line_band",
                    "reason": "fifteen_minute_coil_ready",
                },
                {
                    "instrument": "DAILY-FALLBACK-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "mature_15m_coil",
                    "direction": "short",
                    "daily_bias": "long",
                    "four_hour_context_direction": "unknown",
                    "current_close": 101.0,
                    "current_close_vs_six_lines": "above_all",
                    "reason": "fifteen_minute_coil_ready",
                },
            ],
        )

        result = load_dashboard(self.temp_dir)
        cards = {
            card["instrument"]: card
            for stage in result["stages"].values()
            for card in stage
        }

        bottom = cards["BOTTOM-USDT-SWAP"]
        self.assertEqual(bottom["latest_price"], 0.00123456)
        self.assertEqual(bottom["countertrend_signal"], "bottoming_observation")
        self.assertEqual(bottom["countertrend_signal_label"], "抄底观察")
        self.assertEqual(
            bottom["countertrend_signal_detail"], "15m逆势下探，等止跌确认"
        )
        self.assertEqual(bottom["risk_labels_display"], ["均线已经发散"])

        top = cards["TOP-USDT-SWAP"]
        self.assertEqual(top["latest_price"], 123.45)
        self.assertEqual(top["countertrend_signal"], "topping_observation")
        self.assertEqual(top["countertrend_signal_label"], "摸顶观察")
        self.assertEqual(
            top["countertrend_signal_detail"], "15m逆势冲高，等转弱确认"
        )

        no_signal = cards["NO-SIGNAL-USDT-SWAP"]
        self.assertIsNone(no_signal["countertrend_signal"])
        self.assertIsNone(no_signal["countertrend_signal_label"])
        self.assertIsNone(no_signal["countertrend_signal_detail"])
        self.assertIsNone(cards["INSIDE-BAND-USDT-SWAP"]["countertrend_signal"])
        self.assertIsNone(cards["DAILY-FALLBACK-USDT-SWAP"]["countertrend_signal"])

    def test_state_quality_is_authoritative_when_coil_score_is_zero(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "STATE-QUALITY-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "entry_confirmed",
                    "direction": "long",
                    "daily_bias": "long",
                    "four_hour_context": "trend_aligned",
                    "four_hour_context_score": 0,
                    "fifteen_minute_coil_score": 0,
                    "fifteen_minute_state_quality": 80,
                    "reason": "first_pullback_held",
                }
            ],
        )

        result = load_dashboard(self.temp_dir)
        cards = result["stages"]["entry_confirmed"]

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["entry_readiness"], 83)

    def test_state_and_unknown_codes_use_chinese_display_fallbacks(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "DISPLAY-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "mature_15m_coil",
                    "direction": "mystery_direction",
                    "daily_bias": "mystery_daily",
                    "four_hour_context": "mystery_context",
                    "fifteen_minute_state": "breakout_long",
                    "risk_labels": ["mystery_risk"],
                    "fifteen_minute_coil_score": 80,
                    "reason": "fifteen_minute_coil_ready",
                },
                {
                    "instrument": "UNKNOWN-STATE-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "mature_15m_coil",
                    "direction": "long",
                    "daily_bias": "long",
                    "four_hour_context": "trend_aligned",
                    "fifteen_minute_state": "mystery_state",
                    "fifteen_minute_coil_score": 80,
                    "reason": "fifteen_minute_coil_ready",
                },
            ],
        )

        result = load_dashboard(self.temp_dir)
        cards = {
            card["instrument"]: card
            for stage in result["stages"].values()
            for card in stage
        }

        display = cards["DISPLAY-USDT-SWAP"]
        self.assertEqual(display["fifteen_minute_state_label"], "向上突破")
        self.assertEqual(display["direction_label"], "方向待定")
        self.assertEqual(display["daily_bias_label"], "方向待定")
        self.assertEqual(display["four_hour_context_label"], "高周期不明确")
        self.assertEqual(display["risk_labels_display"], ["其他风险"])
        self.assertEqual(
            cards["UNKNOWN-STATE-USDT-SWAP"]["fifteen_minute_state_label"],
            "状态待确认",
        )

    def test_missing_fifteen_minute_score_is_hidden(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "MISSING-SCORE-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                }
            ],
            default_fifteen_minute_score=None,
        )

        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["stages"]["entry_confirmed"], [])

    def test_visibility_requires_positive_15m_score_and_rejects_invalid_reasons(self) -> None:
        rows = []
        for index, (stage, reason) in enumerate(
            [
                ("entry_confirmed", "first_pullback_held"),
                ("wait_first_pullback", "fifteen_minute_breakout_waiting_pullback"),
                ("entry_confirmed", "first_pullback_failed"),
                ("wait_first_pullback", "fifteen_minute_setup_expired"),
                ("entry_confirmed", "invalid_state"),
            ]
        ):
            rows.append(
                {
                    "instrument": f"CASE-{index}-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": stage,
                    "reason": reason,
                    "fifteen_minute_coil_score": 0 if index == 0 else 80,
                }
            )
        write_report_fixture(self.temp_dir, rows=rows)

        result = load_dashboard(self.temp_dir)
        visible = {
            card["instrument"]
            for stage in result["stages"].values()
            for card in stage
        }

        self.assertEqual(visible, {"CASE-1-USDT-SWAP"})

    def test_load_dashboard_groups_four_layers_filters_invalid_and_caps_observations(
        self,
    ) -> None:
        rows = [
            {
                "instrument": "ENTRY-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "entry_confirmed",
                "reason": "first_pullback_held",
                "bitget_available": True,
            },
            {
                "instrument": "WAIT-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "wait_first_pullback",
                "reason": "fifteen_minute_breakout_waiting_pullback",
                "bitget_available": True,
            },
        ]
        rows.extend(
            {
                "instrument": f"MATURE-{index:02d}-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "mature_15m_coil",
                "reason": "fifteen_minute_coil_ready",
                "fifteen_minute_coil_score": 100 - index,
            }
            for index in range(12)
        )
        rows.extend(
            {
                "instrument": f"FORMING-{index:02d}-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "forming_15m_coil",
                "reason": "fifteen_minute_coil_forming",
                "fifteen_minute_coil_score": 100 - index,
            }
            for index in range(12)
        )
        rows.extend(
            [
                {
                    "instrument": "ZERO-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "mature_15m_coil",
                    "reason": "fifteen_minute_coil_ready",
                    "fifteen_minute_coil_score": 0,
                },
                {
                    "instrument": "EXPIRED-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "forming_15m_coil",
                    "reason": "fifteen_minute_setup_expired",
                    "fifteen_minute_coil_score": 80,
                },
                {
                    "instrument": "INVALID-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "none",
                    "reason": "expired_or_invalid",
                    "fifteen_minute_coil_score": 80,
                },
                {
                    "instrument": "INELIGIBLE-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": False,
                    "stage": "mature_15m_coil",
                    "reason": "fifteen_minute_coil_ready",
                    "fifteen_minute_coil_score": 99,
                },
            ]
        )
        write_report_fixture(
            self.temp_dir,
            rows=rows,
        )
        manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        manifest["observationCounts"] = {"cap": 20}
        self._manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = load_dashboard(self.temp_dir)

        self.assertEqual(
            set(result["stages"]),
            {
                "entry_confirmed",
                "wait_first_pullback",
                "mature_15m_coil",
                "forming_15m_coil",
            },
        )
        self.assertEqual(
            [item["instrument"] for item in result["stages"]["entry_confirmed"]],
            ["ENTRY-USDT-SWAP"],
        )
        self.assertEqual(
            [item["instrument"] for item in result["stages"]["wait_first_pullback"]],
            ["WAIT-USDT-SWAP"],
        )
        observations = result["stages"]["mature_15m_coil"] + result["stages"]["forming_15m_coil"]
        self.assertEqual(len(observations), 20)
        visible = {item["instrument"] for item in observations}
        self.assertNotIn("ZERO-USDT-SWAP", visible)
        self.assertNotIn("EXPIRED-USDT-SWAP", visible)
        self.assertNotIn("INVALID-USDT-SWAP", visible)
        self.assertNotIn("INELIGIBLE-USDT-SWAP", visible)
        self.assertNotIn("near_stage", result)
        self.assertTrue(result["featured"])
        self.assertLessEqual(len(result["featured"]), FEATURED_CAP)

    def test_featured_surfaces_best_cross_layer_cards_before_categories(self) -> None:
        rows = [
            {
                "instrument": "ENTRY-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "entry_confirmed",
                "direction": "long",
                "daily_bias": "long",
                "four_hour_context": "unknown",
                "four_hour_context_score": 0,
                "fifteen_minute_coil_score": 40,
                "reason": "first_pullback_held",
            },
            {
                "instrument": "WAIT-BEST-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "wait_first_pullback",
                "direction": "short",
                "daily_bias": "short",
                "four_hour_context": "trend_aligned",
                "four_hour_context_direction": "short",
                "four_hour_context_score": 80,
                "fifteen_minute_coil_score": 95,
                "playbook": "pullback_20",
                "planned_rr": 3.5,
                "odds_ok": True,
                "target_price": 1.2,
                "reason": "fifteen_minute_breakout_waiting_pullback",
            },
            {
                "instrument": "WAIT-LOW-RR-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "wait_first_pullback",
                "direction": "short",
                "daily_bias": "short",
                "four_hour_context": "trend_aligned",
                "four_hour_context_direction": "short",
                "fifteen_minute_coil_score": 99,
                "playbook": "pullback_20",
                "planned_rr": 1.4,
                "odds_ok": False,
                "reason": "fifteen_minute_breakout_waiting_pullback",
            },
            {
                "instrument": "WAIT-RISKY-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "wait_first_pullback",
                "direction": "long",
                "daily_bias": "short",
                "four_hour_context": "opposite",
                "four_hour_context_score": 0,
                "risk_labels": ["opposite", "expanded_risk"],
                "fifteen_minute_coil_score": 99,
                "reason": "fifteen_minute_breakout_waiting_pullback",
            },
            {
                "instrument": "MATURE-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "mature_15m_coil",
                "direction": "short",
                "daily_bias": "short",
                "four_hour_context": "trend_aligned",
                "four_hour_context_score": 70,
                "fifteen_minute_coil_score": 90,
                "reason": "fifteen_minute_coil_ready",
            },
        ]
        rows.extend(
            {
                "instrument": f"MATURE-FILL-{index:02d}-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "mature_15m_coil",
                "direction": "short",
                "daily_bias": "short",
                "four_hour_context": "trend_aligned",
                "four_hour_context_score": 60,
                "fifteen_minute_coil_score": 88 - index,
                "reason": "fifteen_minute_coil_ready",
            }
            for index in range(8)
        )
        rows.extend(
            {
                "instrument": f"FORMING-{index:02d}-USDT-SWAP",
                "role": "trade",
                "scanner_eligible": True,
                "stage": "forming_15m_coil",
                "direction": "long",
                "daily_bias": "short",
                "four_hour_context": "opposite",
                "four_hour_context_score": 0,
                "risk_labels": ["opposite"],
                "fifteen_minute_coil_score": 80 - index,
                "reason": "fifteen_minute_coil_forming",
            }
            for index in range(4)
        )
        write_report_fixture(self.temp_dir, rows=rows)

        result = load_dashboard(self.temp_dir)
        featured_ids = [card["instrument"] for card in result["featured"]]

        self.assertEqual(featured_ids[0], "WAIT-BEST-USDT-SWAP")
        self.assertIn("MATURE-USDT-SWAP", featured_ids)
        self.assertNotIn("WAIT-RISKY-USDT-SWAP", featured_ids[:3])
        self.assertEqual(len(featured_ids), FEATURED_CAP)
        self.assertEqual(len(result["stages"]["forming_15m_coil"]), 4)
        self.assertEqual(
            [card["instrument"] for card in result["stages"]["entry_confirmed"]],
            ["ENTRY-USDT-SWAP"],
        )

    def test_featured_still_lists_opposite_pullbacks_when_no_high_odds(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "PEPE-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "wait_first_pullback",
                    "direction": "long",
                    "daily_bias": "short",
                    "four_hour_context": "opposite",
                    "four_hour_context_direction": "short",
                    "risk_labels": ["expanded_risk", "opposite"],
                    "playbook": "coil_retest",
                    "planned_rr": 9.14,
                    "odds_ok": False,
                    "fifteen_minute_coil_score": 80,
                    "reason": "fifteen_minute_breakout_waiting_pullback",
                },
                {
                    "instrument": "FLOKI-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "wait_first_pullback",
                    "direction": "long",
                    "daily_bias": "short",
                    "four_hour_context": "opposite",
                    "playbook": "coil_retest",
                    "planned_rr": 6.16,
                    "odds_ok": False,
                    "fifteen_minute_coil_score": 70,
                    "reason": "fifteen_minute_breakout_waiting_pullback",
                },
            ],
        )

        result = load_dashboard(self.temp_dir)
        featured_ids = [card["instrument"] for card in result["featured"]]

        self.assertEqual(set(featured_ids), {"PEPE-USDT-SWAP", "FLOKI-USDT-SWAP"})

    def test_movers_board_ranks_eligible_24h_changes_and_skips_ineligible(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "UP-A-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "none",
                    "reason": "no_active_three_stage_setup",
                    "current_close": 12.0,
                    "change_24h_pct": 9.5,
                    "bitget_available": True,
                    "fifteen_minute_coil_score": 0,
                },
                {
                    "instrument": "UP-B-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "mature_15m_coil",
                    "reason": "fifteen_minute_coil_ready",
                    "current_close": 3.0,
                    "change_24h_pct": 2.25,
                    "bitget_available": True,
                    "fifteen_minute_coil_score": 80,
                },
                {
                    "instrument": "DOWN-A-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "wait_first_pullback",
                    "reason": "fifteen_minute_breakout_waiting_pullback",
                    "current_close": 1.1,
                    "change_24h_pct": -8.4,
                    "bitget_available": True,
                    "fifteen_minute_coil_score": 70,
                },
                {
                    "instrument": "DOWN-B-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "none",
                    "reason": "no_active_three_stage_setup",
                    "current_close": 8.0,
                    "change_24h_pct": -1.1,
                    "fifteen_minute_coil_score": 0,
                },
                {
                    "instrument": "FLAT-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "none",
                    "reason": "no_active_three_stage_setup",
                    "change_24h_pct": 0.0,
                    "fifteen_minute_coil_score": 0,
                },
                {
                    "instrument": "SKIP-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": False,
                    "stage": "none",
                    "reason": "not_eligible",
                    "change_24h_pct": 40.0,
                    "fifteen_minute_coil_score": 0,
                },
            ],
        )

        result = load_dashboard(self.temp_dir)
        gainers = [card["instrument"] for card in result["movers"]["gainers"]]
        losers = [card["instrument"] for card in result["movers"]["losers"]]

        self.assertEqual(gainers, ["UP-A-USDT-SWAP", "UP-B-USDT-SWAP"])
        self.assertEqual(losers, ["DOWN-A-USDT-SWAP", "DOWN-B-USDT-SWAP"])
        self.assertEqual(result["movers"]["gainers"][0]["change_24h_pct"], 9.5)
        self.assertTrue(
            str(result["movers"]["gainers"][0]["bitget_url"]).endswith("UPAUSDT")
        )
        self.assertLessEqual(len(result["movers"]["gainers"]), MOVERS_CAP)
        self.assertEqual(MOVERS_CAP, 9)
        self.assertEqual(MOMENTUM_CAP, 9)
        self.assertNotIn("SKIP-USDT-SWAP", gainers)
        self.assertNotIn("FLAT-USDT-SWAP", gainers + losers)
        self.assertEqual(
            result["stages"]["mature_15m_coil"][0]["change_24h_pct"],
            2.25,
        )
        self.assertEqual(
            result["stages"]["wait_first_pullback"][0]["change_24h_pct"],
            -8.4,
        )

    def test_momentum_board_keeps_launched_and_watch_on_the_same_page(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "SNDK-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "none",
                    "reason": "no_active_three_stage_setup",
                    "direction": "long",
                    "current_close": 1643.92,
                    "change_24h_pct": 6.1,
                    "bitget_available": True,
                    "momentum_status": "momentum_up",
                    "momentum_candidate": True,
                    "daily_streak": 3,
                    "four_hour_streak": 4,
                    "volume_ratio": 2.2,
                    "momentum_score": 40,
                    "fifteen_minute_coil_score": 0,
                },
                {
                    "instrument": "WATCH-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "none",
                    "reason": "no_active_three_stage_setup",
                    "direction": "long",
                    "current_close": 1.2,
                    "change_24h_pct": 3.1,
                    "bitget_available": True,
                    "momentum_status": "watch_up",
                    "momentum_candidate": False,
                    "daily_streak": 2,
                    "volume_ratio": 1.1,
                    "momentum_score": 12,
                    "fifteen_minute_coil_score": 0,
                },
                {
                    "instrument": "SKIP-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": False,
                    "stage": "none",
                    "reason": "not_eligible",
                    "momentum_status": "momentum_up",
                    "momentum_score": 99,
                    "fifteen_minute_coil_score": 0,
                },
            ],
        )

        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["momentum"]["launched"][0]["instrument"], "SNDK-USDT-SWAP")
        self.assertEqual(result["momentum"]["launched"][0]["status_label"], "连续上涨且放量")
        self.assertEqual(result["momentum"]["watch"][0]["instrument"], "WATCH-USDT-SWAP")
        self.assertNotIn(
            "SKIP-USDT-SWAP",
            [card["instrument"] for card in result["momentum"]["launched"]],
        )

    def test_movers_and_momentum_boards_keep_nine_cards(self) -> None:
        rows = []
        for index in range(12):
            rows.append(
                {
                    "instrument": f"UP-{index:02d}-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "none",
                    "reason": "no_active_three_stage_setup",
                    "current_close": 10 + index,
                    "change_24h_pct": 12 - index * 0.1,
                    "bitget_available": True,
                    "momentum_status": "momentum_up",
                    "momentum_candidate": True,
                    "momentum_score": 50 - index,
                    "fifteen_minute_coil_score": 0,
                }
            )
            rows.append(
                {
                    "instrument": f"DN-{index:02d}-USDT-SWAP",
                    "role": "trade",
                    "scanner_eligible": True,
                    "stage": "none",
                    "reason": "no_active_three_stage_setup",
                    "current_close": 9 - index * 0.1,
                    "change_24h_pct": -1 - index * 0.1,
                    "bitget_available": True,
                    "momentum_status": "watch_up",
                    "momentum_candidate": False,
                    "momentum_score": 40 - index,
                    "fifteen_minute_coil_score": 0,
                }
            )
        write_report_fixture(self.temp_dir, rows=rows)

        result = load_dashboard(self.temp_dir)

        self.assertEqual(len(result["movers"]["gainers"]), 9)
        self.assertEqual(len(result["movers"]["losers"]), 9)
        self.assertEqual(len(result["momentum"]["launched"]), 9)
        self.assertEqual(len(result["momentum"]["watch"]), 9)
        self.assertEqual(result["movers"]["gainers"][0]["instrument"], "UP-00-USDT-SWAP")
        self.assertEqual(result["momentum"]["launched"][0]["instrument"], "UP-00-USDT-SWAP")

    def test_missing_report_returns_first_scan_empty_state(self) -> None:
        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["summary"]["status"], "not_scanned")
        self.assertEqual(result["stages"]["entry_confirmed"], [])
        self.assertEqual(result["featured"], [])
        self.assertEqual(result["movers"], {"gainers": [], "losers": []})
        self.assertEqual(result["momentum"], {"launched": [], "watch": []})

    def test_empty_manifest_returns_unavailable_without_leaking_details(self) -> None:
        write_report_fixture(self.temp_dir, rows=[])
        self._manifest_path.write_text("", encoding="utf-8")

        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["summary"]["status"], "report_unavailable")
        self.assertEqual(result["diagnostics"]["reason"], "invalid_manifest")
        self.assertNotIn(str(self.temp_dir), repr(result))

    def test_wrong_shape_manifest_returns_unavailable_without_leaking_details(self) -> None:
        write_report_fixture(self.temp_dir, rows=[])
        self._manifest_path.write_text(
            json.dumps(["private-manifest-value"]), encoding="utf-8"
        )

        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["summary"]["status"], "report_unavailable")
        self.assertEqual(result["diagnostics"]["reason"], "invalid_manifest")
        self.assertNotIn("private-manifest-value", repr(result))

    def test_empty_csv_returns_unavailable_without_leaking_details(self) -> None:
        write_report_fixture(self.temp_dir, rows=[])
        self._csv_path.write_text("", encoding="utf-8")

        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["summary"]["status"], "report_unavailable")
        self.assertEqual(result["diagnostics"]["reason"], "invalid_csv")
        self.assertNotIn(str(self.temp_dir), repr(result))

    def test_malformed_csv_returns_unavailable_without_leaking_details(self) -> None:
        write_report_fixture(self.temp_dir, rows=[])
        self._csv_path.write_text(
            'instrument,role\n"A-USDT-SWAP,trade\n', encoding="utf-8"
        )

        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["summary"]["status"], "report_unavailable")
        self.assertEqual(result["diagnostics"]["reason"], "invalid_csv")
        self.assertNotIn(str(self.temp_dir), repr(result))

    def test_csv_missing_required_columns_is_not_reported_ready(self) -> None:
        self._manifest_path.write_text(
            json.dumps(
                {
                    "retrievedAt": "2026-08-12T00:30:00+00:00",
                    "liveUsdtSwaps": 1,
                    "eligibleTradeContracts": 1,
                    "errors": {},
                }
            ),
            encoding="utf-8",
        )
        self._csv_path.write_text(
            "instrument,role\nA-USDT-SWAP,trade\n", encoding="utf-8"
        )

        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["summary"]["status"], "report_unavailable")
        self.assertEqual(result["diagnostics"]["reason"], "invalid_csv_schema")
        self.assertNotIn(str(self.temp_dir), repr(result))

    def test_missing_values_are_json_safe_and_not_nan(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "NAN-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                    "four_hour_coil_score": float("nan"),
                    "fifteen_minute_coil_score": float("nan"),
                    "fifteen_minute_state_quality": 1,
                    "four_hour_breakout_at": pd.NaT,
                }
            ],
        )

        result = load_dashboard(self.temp_dir)

        json.dumps(result, allow_nan=False)
        card = result["stages"]["entry_confirmed"][0]
        self.assertIsNone(card["four_hour_coil_score"])
        self.assertIsNone(card["fifteen_minute_coil_score"])
        self.assertIsNone(card["four_hour_breakout_at"])

    def test_load_dashboard_sorts_scores_and_exposes_manifest_diagnostics(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "Z-USDT-SWAP",
                    "role": "trade",
                    "stage": "forming_15m_coil",
                    "reason": "watching",
                    "four_hour_coil_score": 30,
                    "fifteen_minute_coil_score": 10,
                    "four_hour_breakout_at": "2026-08-12T00:00:00+00:00",
                    "bitget_available": False,
                },
                {
                    "instrument": "A-USDT-SWAP",
                    "role": "trade",
                    "stage": "forming_15m_coil",
                    "reason": "watching",
                    "four_hour_coil_score": 25,
                    "fifteen_minute_coil_score": 20,
                    "four_hour_breakout_at": "2026-08-12T00:30:00+00:00",
                },
                {
                    "instrument": "ENTRY-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                    "four_hour_coil_score": 1,
                    "fifteen_minute_coil_score": 1,
                    "four_hour_breakout_at": "2026-08-12T00:00:00+00:00",
                    "bitget_available": True,
                },
            ],
        )

        result = load_dashboard(self.temp_dir)

        self.assertEqual(
            [
                item["instrument"]
                for item in result["stages"]["forming_15m_coil"]
            ],
            ["A-USDT-SWAP", "Z-USDT-SWAP"],
        )
        card = result["stages"]["entry_confirmed"][0]
        self.assertEqual(
            card["bitget_url"],
            "https://www.bitget.com/zh-CN/futures/usdt/ENTRYUSDT",
        )
        self.assertIsNone(
            next(item for item in result["stages"]["forming_15m_coil"] if item["instrument"] == "Z-USDT-SWAP")["bitget_url"]
        )
        self.assertEqual(card["four_hour_breakout_at"], "2026-08-12 08:00")
        self.assertEqual(result["summary"]["retrieved_at_shanghai"], "2026-08-12 08:30")
        self.assertEqual(result["summary"]["live_usdt_swaps"], 3)
        self.assertEqual(result["summary"]["eligible_trade_contracts"], 2)
        self.assertEqual(result["summary"]["error_count"], 1)
        self.assertEqual(result["diagnostics"]["error_count"], 1)

    def test_load_dashboard_discards_input_bitget_url_and_handles_missing_instrument(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "SAFE-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                    "bitget_url": "javascript:alert('injected')",
                    "bitget_available": True,
                },
                {
                    "instrument": None,
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                    "bitget_url": "https://attacker.invalid/steal",
                },
            ],
        )

        result = load_dashboard(self.temp_dir)
        cards = result["stages"]["entry_confirmed"]
        cards_by_instrument = {card["instrument"]: card for card in cards}

        self.assertEqual(
            cards_by_instrument["SAFE-USDT-SWAP"]["bitget_url"],
            "https://www.bitget.com/zh-CN/futures/usdt/SAFEUSDT",
        )
        self.assertIsNone(cards_by_instrument[None]["bitget_url"])

    def test_load_dashboard_redacts_sensitive_manifest_errors(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "A-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                }
            ],
        )
        manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        manifest["errors"] = {
            "network": "Authorization: Bearer supersecret",
            "equals": "Authorization=Bearer manifest-equals-secret",
            "details": {
                "token": "nested-secret",
                "authorization": "auth-manifest-secret",
                "api_key": "api-manifest-secret",
                "raw": 'api_key="manifest-spaced-secret"',
            },
        }
        self._manifest_path.write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        result = load_dashboard(self.temp_dir)
        encoded = json.dumps(result, ensure_ascii=False)

        self.assertNotIn("supersecret", encoded)
        self.assertNotIn("manifest-equals-secret", encoded)
        self.assertNotIn("nested-secret", encoded)
        self.assertNotIn("auth-manifest-secret", encoded)
        self.assertNotIn("api-manifest-secret", encoded)
        self.assertNotIn("manifest-spaced-secret", encoded)

    def test_load_dashboard_exposes_scanner_filter_diagnostics(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "A-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                    "bitget_available": True,
                }
            ],
        )
        manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        manifest["scannerUniverse"] = {
            "instCategory": "1",
            "bitgetContractIntersection": True,
        }
        manifest["scannerExclusionCounts"] = {"stablecoin_base": 4}
        self._manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = load_dashboard(self.temp_dir)

        self.assertEqual(result["diagnostics"]["scanner_universe"]["instCategory"], "1")
        self.assertEqual(result["diagnostics"]["scanner_exclusion_counts"]["stablecoin_base"], 4)


class ScanManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_scan_manager_allows_only_one_active_scan(self) -> None:
        release = threading.Event()
        manager = ScanManager(runner=lambda progress: release.wait(timeout=2))

        self.assertTrue(manager.start())
        self.assertFalse(manager.start())
        release.set()
        manager.wait(timeout=2)

        self.assertEqual(manager.snapshot()["state"], "succeeded")

    def test_failed_scan_reports_error_without_removing_report(self) -> None:
        report = self.temp_dir / "latest.csv"
        report.write_text(
            "instrument,stage\nOLD-USDT-SWAP,none\n", encoding="utf-8"
        )
        manager = ScanManager(
            runner=lambda progress: (_ for _ in ()).throw(
                RuntimeError("network failed")
            )
        )

        manager.start()
        manager.wait(timeout=2)

        self.assertEqual(manager.snapshot()["state"], "failed")
        self.assertTrue(report.exists())

    def test_scan_logs_redact_exchange_secret_values_and_quoted_keys(self) -> None:
        secret_values = {
            "api-secret",
            "okx-secret",
            "client-secret",
            "access-secret",
            "passphrase-secret",
            "json-secret",
        }
        lines = [
            "OKX_API_KEY=api-secret",
            "OKX_SECRET_KEY=okx-secret",
            "client_secret=client-secret",
            "access_token=access-secret",
            "PASSPHRASE=passphrase-secret",
            '{"api_key":"json-secret"}',
        ]

        def runner(progress):
            for line in lines:
                progress(line)
            raise RuntimeError("failed after logging credentials")

        manager = ScanManager(runner=runner)
        manager.start()
        manager.wait(timeout=2)

        snapshot = manager.snapshot()
        self.assertEqual(snapshot["state"], "failed")
        for secret in secret_values:
            self.assertNotIn(secret, repr(snapshot))

    def test_scan_redacts_bearer_and_spaced_sensitive_assignments_everywhere(self) -> None:
        lines = [
            "Authorization=Bearer auth-equals-secret",
            "Authorization:Basic auth-basic-secret",
            "OKX_API_KEY=Bearer api-bearer-secret",
            'api_key="secret value with spaces"',
        ]
        secrets = {
            "auth-equals-secret",
            "auth-basic-secret",
            "api-bearer-secret",
            "secret value with spaces",
        }

        for line in lines:
            secret = next(secret for secret in secrets if secret in line)
            self.assertNotIn(secret, clean_log_line(line))

        def runner(progress):
            for line in lines:
                progress(line)
            raise RuntimeError("Authorization=Bearer exception-secret")

        manager = ScanManager(runner=runner)
        manager.start()
        manager.wait(timeout=2)
        snapshot = manager.snapshot()

        self.assertEqual(snapshot["state"], "failed")
        encoded = repr(snapshot)
        for secret in (*secrets, "exception-secret"):
            self.assertNotIn(secret, encoded)

    def test_scan_tracks_stage_bounds_logs_and_uses_utc_timestamps(self) -> None:
        def runner(progress):
            progress("[1/3] daily")
            progress("[2/3] four hour")
            progress("[3/3] fifteen minute")
            for index in range(45):
                progress(f"line-{index}")

        manager = ScanManager(runner=runner)
        manager.start()
        manager.wait(timeout=2)

        snapshot = manager.snapshot()
        self.assertEqual(snapshot["state"], "succeeded")
        self.assertEqual(snapshot["stage"], "fifteen_minute")
        self.assertEqual(len(snapshot["log_tail"]), 40)
        self.assertEqual(snapshot["log_tail"][0], "line-5")
        self.assertTrue(snapshot["started_at"].endswith("+00:00"))
        self.assertTrue(snapshot["finished_at"].endswith("+00:00"))

    def test_thread_start_failure_does_not_leave_manager_running(self) -> None:
        def fail_thread(**kwargs):
            raise RuntimeError("thread start failed")

        manager = ScanManager(
            runner=lambda progress: None,
            thread_factory=fail_thread,
        )

        self.assertFalse(manager.start())
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["state"], "failed")
        self.assertFalse(snapshot["error"] is None)
        self.assertIsNotNone(snapshot["finished_at"])
        self.assertFalse(manager.start())


class ProductionScanTests(unittest.TestCase):
    class _FakeStdout:
        def __init__(self, lines=None, error=None, on_iter=None, close_error=None):
            self._lines = list(lines or [])
            self._error = error
            self._on_iter = on_iter
            self._close_error = close_error
            self.closed = False

        def __iter__(self):
            if self._on_iter is not None:
                self._on_iter()
            if self._error is not None:
                raise self._error
            return iter(self._lines)

        def close(self):
            self.closed = True
            if self._close_error is not None:
                raise self._close_error

    class _FakeProcess:
        def __init__(self, stdout, returncode=0, timeout_on_wait=False):
            self.stdout = stdout
            self.returncode = returncode
            self.terminate_calls = 0
            self.kill_calls = 0
            self.wait_calls = []
            self.timeout_on_wait = timeout_on_wait
            self._running = True

        def poll(self):
            return None if self._running else self.returncode

        def terminate(self):
            self.terminate_calls += 1
            self._running = False

        def kill(self):
            self.kill_calls += 1
            self._running = False

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if (
                self.timeout_on_wait
                and timeout is not None
                and len(self.wait_calls) == 2
            ):
                raise subprocess.TimeoutExpired("fake", timeout)
            if self.timeout_on_wait and timeout is None:
                return self.returncode
            self._running = False
            return self.returncode

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.report_dir = Path(self._tempdir.name) / "okx-three-stage"
        self.report_dir.mkdir()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _write_artifacts(self, values: dict[str, bytes]) -> None:
        for name, value in values.items():
            (self.report_dir / name).write_bytes(value)

    def _write_artifacts_to(self, output_dir: Path, values: dict[str, bytes]) -> None:
        for name, value in values.items():
            (output_dir / name).write_bytes(value)

    def _patch_report_dir(self):
        return patch(
            "dashboard.scan_manager.PRODUCTION_REPORT_DIR", self.report_dir
        )

    def test_stream_error_terminates_and_waits_for_process(self) -> None:
        process = self._FakeProcess(
            self._FakeStdout(error=RuntimeError("stream failed"))
        )
        manager = ScanManager(
            runner=lambda progress: run_production_scan(progress)
        )

        with patch("dashboard.scan_manager.subprocess.Popen", return_value=process):
            with self._patch_report_dir():
                manager.start()
                manager.wait(timeout=2)

        self.assertEqual(manager.snapshot()["state"], "failed")
        self.assertGreaterEqual(process.terminate_calls, 1)
        self.assertGreaterEqual(len(process.wait_calls), 1)

    def test_stdout_close_error_still_reaps_process(self) -> None:
        process = self._FakeProcess(
            self._FakeStdout(close_error=RuntimeError("close failed"))
        )
        manager = ScanManager(
            runner=lambda progress: run_production_scan(progress)
        )

        with patch("dashboard.scan_manager.subprocess.Popen", return_value=process):
            with self._patch_report_dir():
                manager.start()
                manager.wait(timeout=2)

        self.assertEqual(manager.snapshot()["state"], "failed")
        self.assertGreaterEqual(len(process.wait_calls), 2)

    def test_progress_error_terminates_and_waits_for_process(self) -> None:
        process = self._FakeProcess(self._FakeStdout(lines=["line\n"]))

        def runner(progress):
            def fail_progress(line):
                raise RuntimeError("progress failed")

            run_production_scan(fail_progress)

        manager = ScanManager(runner=runner)
        with patch("dashboard.scan_manager.subprocess.Popen", return_value=process):
            with self._patch_report_dir():
                manager.start()
                manager.wait(timeout=2)

        self.assertEqual(manager.snapshot()["state"], "failed")
        self.assertGreaterEqual(process.terminate_calls, 1)
        self.assertGreaterEqual(len(process.wait_calls), 1)

    def test_nonzero_process_fails_manager_after_wait(self) -> None:
        process = self._FakeProcess(self._FakeStdout(), returncode=7)
        manager = ScanManager(
            runner=lambda progress: run_production_scan(progress)
        )
        with patch("dashboard.scan_manager.subprocess.Popen", return_value=process):
            with self._patch_report_dir():
                manager.start()
                manager.wait(timeout=2)

        self.assertEqual(manager.snapshot()["state"], "failed")
        self.assertGreaterEqual(len(process.wait_calls), 1)

    def test_process_timeout_kills_then_waits_again(self) -> None:
        process = self._FakeProcess(
            self._FakeStdout(), timeout_on_wait=True
        )
        def fake_popen(command, **kwargs):
            output_dir = Path(command[command.index("--output-dir") + 1])
            for name in ("LATEST.md", "latest.csv", "run-manifest.json"):
                (output_dir / name).write_bytes(b"staged\n")
            return process

        with patch("dashboard.scan_manager.subprocess.Popen", side_effect=fake_popen):
            with self._patch_report_dir():
                run_production_scan(lambda line: None)

        self.assertEqual(process.kill_calls, 1)
        self.assertGreaterEqual(len(process.wait_calls), 2)

    def test_production_scan_uses_fixed_command_and_working_directory(self) -> None:
        process = self._FakeProcess(self._FakeStdout())

        def fake_popen(command, **kwargs):
            output_dir = Path(command[command.index("--output-dir") + 1])
            for name in ("LATEST.md", "latest.csv", "run-manifest.json"):
                (output_dir / name).write_bytes(b"staged\n")
            return process

        with patch(
            "dashboard.scan_manager.subprocess.Popen", side_effect=fake_popen
        ) as popen:
            with self._patch_report_dir():
                run_production_scan(lambda line: None)

        command = popen.call_args.args[0]
        self.assertEqual(command[1], "/freqtrade/scripts/scan_okx_three_stage.py")
        self.assertEqual(command[-6:], [
            "--output-dir",
            command[command.index("--output-dir") + 1],
            "--workers",
            "8",
            "--requests-per-second",
            "12",
        ])
        self.assertNotEqual(command[command.index("--output-dir") + 1], str(self.report_dir))
        self.assertEqual(
            Path(command[command.index("--output-dir") + 1]).parent,
            self.report_dir.parent,
        )
        self.assertEqual(popen.call_args.kwargs["cwd"], "/freqtrade")

    def test_production_scan_uses_isolated_output_and_cleans_staging_directory(self) -> None:
        old = {
            "LATEST.md": b"old markdown\n",
            "latest.csv": b"old csv\n",
            "run-manifest.json": b"old manifest\n",
        }
        new = {
            "LATEST.md": b"new markdown\n",
            "latest.csv": b"new csv\n",
            "run-manifest.json": b"new manifest\n",
        }
        self._write_artifacts(old)
        captured: dict[str, str] = {}
        observed_live_during_scan: dict[str, bytes] = {}

        def create_staged_artifacts() -> None:
            output_dir = Path(captured["output_dir"])
            for name in old:
                observed_live_during_scan[name] = (self.report_dir / name).read_bytes()
            for name, value in new.items():
                (output_dir / name).write_bytes(value)

        process = self._FakeProcess(
            self._FakeStdout(on_iter=create_staged_artifacts)
        )

        def fake_popen(command, **kwargs):
            captured["output_dir"] = command[command.index("--output-dir") + 1]
            return process

        with patch("dashboard.scan_manager.subprocess.Popen", side_effect=fake_popen):
            with self._patch_report_dir():
                run_production_scan(lambda line: None)

        staged_dir = Path(captured["output_dir"])
        self.assertNotEqual(staged_dir, self.report_dir)
        self.assertEqual(staged_dir.parent, self.report_dir.parent)
        self.assertFalse(staged_dir.exists())
        self.assertEqual(observed_live_during_scan, old)
        for name, value in new.items():
            self.assertEqual((self.report_dir / name).read_bytes(), value)
        self.assertEqual(
            list(self.report_dir.parent.glob(".dashboard-scan-output-*")), []
        )

    def test_api_read_waits_for_publish_lock_instead_of_reading_partial_artifacts(self) -> None:
        write_report_fixture(
            self.report_dir,
            rows=[
                {
                    "instrument": "LOCK-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                }
            ],
        )
        app = create_app(
            report_dir=self.report_dir,
            scan_manager=ScanManager(runner=lambda progress: None),
        )
        result: dict[str, object] = {}
        returned = threading.Event()

        def read_dashboard() -> None:
            with TestClient(app) as client:
                result["payload"] = client.get("/api/dashboard").json()
            returned.set()

        with report_artifact_lock():
            reader = threading.Thread(target=read_dashboard)
            reader.start()
            self.assertFalse(returned.wait(timeout=0.1))
        self.assertTrue(returned.wait(timeout=2))
        reader.join(timeout=2)
        payload = result["payload"]
        self.assertEqual(payload["summary"]["status"], "ready")
        self.assertEqual(payload["scan"]["state"], "idle")

    def test_failed_scan_restores_all_existing_report_artifacts(self) -> None:
        old = {
            "LATEST.md": b"old markdown\n",
            "latest.csv": b"old csv\n",
            "run-manifest.json": b"old manifest\n",
        }
        new = {
            "LATEST.md": b"partial markdown\n",
            "latest.csv": b"partial csv\n",
            "run-manifest.json": b"partial manifest\n",
        }
        self._write_artifacts(old)

        captured: dict[str, str] = {}

        def write_new_files():
            self._write_artifacts_to(Path(captured["output_dir"]), new)

        process = self._FakeProcess(
            self._FakeStdout(on_iter=write_new_files), returncode=1
        )
        def fake_popen(command, **kwargs):
            captured["output_dir"] = command[command.index("--output-dir") + 1]
            return process

        with patch(
            "dashboard.scan_manager.subprocess.Popen", side_effect=fake_popen
        ):
            with self._patch_report_dir():
                with self.assertRaises(RuntimeError):
                    run_production_scan(lambda line: None)

        for name, value in old.items():
            self.assertEqual((self.report_dir / name).read_bytes(), value)
        self.assertEqual(
            list(self.report_dir.parent.glob(".dashboard-scan-backup-*")), []
        )
        self.assertEqual(
            list(self.report_dir.parent.glob(".dashboard-scan-output-*")), []
        )

    def test_successful_scan_keeps_new_report_artifacts(self) -> None:
        old = {
            "LATEST.md": b"old markdown\n",
            "latest.csv": b"old csv\n",
            "run-manifest.json": b"old manifest\n",
        }
        new = {
            "LATEST.md": b"new markdown\n",
            "latest.csv": b"new csv\n",
            "run-manifest.json": b"new manifest\n",
        }
        self._write_artifacts(old)
        captured: dict[str, str] = {}
        process = self._FakeProcess(
            self._FakeStdout(
                on_iter=lambda: self._write_artifacts_to(
                    Path(captured["output_dir"]), new
                )
            )
        )

        def fake_popen(command, **kwargs):
            captured["output_dir"] = command[command.index("--output-dir") + 1]
            return process

        with patch(
            "dashboard.scan_manager.subprocess.Popen", side_effect=fake_popen
        ):
            with self._patch_report_dir():
                run_production_scan(lambda line: None)

        for name, value in new.items():
            self.assertEqual((self.report_dir / name).read_bytes(), value)
        self.assertEqual(
            list(self.report_dir.parent.glob(".dashboard-scan-backup-*")), []
        )


class DashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self._tempdir.name)
        self.release = threading.Event()
        self.manager = ScanManager(
            runner=lambda progress: self.release.wait(timeout=2)
        )

    def tearDown(self) -> None:
        self.release.set()
        self.manager.wait(timeout=2)
        self._tempdir.cleanup()

    def test_api_starts_scan_and_rejects_duplicate(self) -> None:
        app = create_app(report_dir=self.temp_dir, scan_manager=self.manager)

        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
            self.assertEqual(client.post("/api/scan").status_code, 202)
            self.assertEqual(client.post("/api/scan").status_code, 409)

    def test_dashboard_api_returns_previous_data_while_scan_failed(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "A-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                }
            ],
        )
        failed_manager = ScanManager(
            runner=lambda progress: (_ for _ in ()).throw(
                RuntimeError("network failed")
            )
        )
        failed_manager.start()
        failed_manager.wait(timeout=2)

        app = create_app(report_dir=self.temp_dir, scan_manager=failed_manager)
        with TestClient(app) as client:
            payload = client.get("/api/dashboard").json()

        self.assertEqual(
            payload["stages"]["entry_confirmed"][0]["instrument"],
            "A-USDT-SWAP",
        )
        self.assertEqual(payload["scan"]["state"], "failed")

    def test_dashboard_api_redacts_manifest_error_values(self) -> None:
        write_report_fixture(
            self.temp_dir,
            rows=[
                {
                    "instrument": "A-USDT-SWAP",
                    "role": "trade",
                    "stage": "entry_confirmed",
                    "reason": "first_pullback_held",
                }
            ],
        )
        manifest_path = self.temp_dir / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["errors"] = {
            "network": "Authorization: Bearer supersecret",
            "equals": "OKX_API_KEY=Bearer api-equals-secret",
            "details": {
                "access_token": "api-secret",
                "authorization": "auth-api-secret",
                "raw": 'api_key="api-spaced-secret"',
            },
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        app = create_app(
            report_dir=self.temp_dir,
            scan_manager=ScanManager(runner=lambda progress: None),
        )
        with TestClient(app) as client:
            response = client.get("/api/dashboard")
            payload = response.json()

        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("supersecret", encoded)
        self.assertNotIn("api-equals-secret", encoded)
        self.assertNotIn("api-secret", encoded)
        self.assertNotIn("auth-api-secret", encoded)
        self.assertNotIn("api-spaced-secret", encoded)

    def test_scan_status_returns_manager_snapshot(self) -> None:
        app = create_app(report_dir=self.temp_dir, scan_manager=self.manager)

        with TestClient(app) as client:
            response = client.get("/api/scan/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "idle")

    def test_home_page_contains_dashboard_mount_points(self) -> None:
        app = create_app(report_dir=self.temp_dir, scan_manager=self.manager)

        with TestClient(app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("brand-mark", response.text)
        self.assertIn('id="scan-now"', response.text)
        self.assertIn('id="featured-section"', response.text)
        self.assertIn('id="featured-cards"', response.text)
        self.assertIn('id="movers-section"', response.text)
        self.assertIn('id="momentum-section"', response.text)
        self.assertIn('id="gainers-cards"', response.text)
        self.assertIn('id="losers-cards"', response.text)
        self.assertIn("涨跌幅榜", response.text)
        self.assertIn("连续涨跌 / 放量", response.text)
        self.assertLess(
            response.text.index('id="movers-section"'),
            response.text.index('id="momentum-section"'),
        )
        self.assertLess(
            response.text.index('id="momentum-section"'),
            response.text.index('id="featured-section"'),
        )
        self.assertIn("重点看", response.text)
        self.assertIn(">阶段<", response.text)
        self.assertIn('/static/dashboard.css', response.text)


class DashboardComposeTests(unittest.TestCase):
    def test_compose_dashboard_is_bound_to_loopback_only(self) -> None:
        compose = yaml.safe_load(
            Path("docker-compose.yml").read_text(encoding="utf-8")
        )
        service = compose["services"]["dashboard"]
        self.assertEqual(service.get("container_name"), "okx-dashboard")
        self.assertIn("127.0.0.1:8787:8787", service["ports"])


class DashboardFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        project_root = Path(__file__).resolve().parents[1]
        cls.index_html = (project_root / "dashboard/templates/index.html").read_text(
            encoding="utf-8"
        )
        cls.css = (project_root / "dashboard/static/dashboard.css").read_text(
            encoding="utf-8"
        )
        cls.js = (project_root / "dashboard/static/dashboard.js").read_text(
            encoding="utf-8"
        )

    def test_state_panels_hidden_attribute_is_not_overridden_by_flex(self) -> None:
        self.assertRegex(
            self.index_html,
            r'id="error-state"[^>]*\bhidden\b',
        )
        self.assertRegex(
            self.index_html,
            r'id="empty-state"[^>]*\bhidden\b',
        )
        self.assertRegex(
            self.css,
            r"\.state-panel\[hidden\]\s*,\s*\[hidden\]\s*\{[^}]*display:\s*none\s*!important",
        )
        self.assertRegex(
            self.js,
            r'if \(summary\.status === "ready"\) \{[\s\S]*?empty\.hidden = candidateCount > 0 \|\| hasMovers\(data\);',
        )

    def test_cards_render_compact_fields_with_fixed_lifecycle_boundary(self) -> None:
        for field in (
            "entry_readiness",
            "entry_readiness_label",
            "stage_label",
            "direction_label",
            "fifteen_minute_state_label",
            "fifteen_minute_coil_score",
            "four_hour_context_label",
            "daily_bias_label",
            "risk_labels_display",
            "fifteen_minute_breakout_at",
            "four_hour_breakout_at",
            "initial_stop_price",
        ):
            self.assertIn(f"item.{field}", self.js)
        for label in (
            "入场准备度",
            "突破时间",
            "初始止损",
        ):
            self.assertIn(label, self.js)
        self.assertIn("textContent", self.js)
        self.assertIn("观察，不是开仓信号", self.index_html)
        for obsolete in ("ready_4h_breakout_15m_coiled", "watch_both_coiled"):
            self.assertNotIn(obsolete, self.js)
            self.assertNotIn(obsolete, self.index_html)

    def test_mobile_styles_keep_compact_facts_and_lifecycle_visible(self) -> None:
        self.assertIn("item.fifteen_minute_breakout_at", self.js)
        self.assertIn("item.initial_stop_price", self.js)
        mobile_css = self.css.split("@media (max-width: 760px)", 1)[1]
        self.assertRegex(mobile_css, r"\.card-list\s*\{[^}]*grid-template-columns:\s*1fr")
        self.assertNotIn(".metric--secondary", mobile_css)
        self.assertNotIn(".metric--zone", mobile_css)
        self.assertNotIn(".event-time", mobile_css)
        self.assertIn(".bitget-button", self.css)

    def test_price_fields_use_adaptive_non_lossy_formatter(self) -> None:
        self.assertIn("function formatPrice", self.js)
        for field in (
            "item.initial_stop_price",
        ):
            self.assertIn(field, self.js)
        self.assertRegex(self.js, r"formatPrice\(value\)")

    def test_scan_price_and_countertrend_signal_have_dedicated_bounded_blocks(
        self,
    ) -> None:
        self.assertIn("latest_price", self.js)
        self.assertIn("current_close", self.js)
        self.assertIn("countertrend_signal_label", self.js)
        self.assertIn("countertrend_signal_detail", self.js)
        self.assertIn("function factsLine", self.js)
        self.assertIn("function flagsLine", self.js)
        self.assertRegex(
            self.css,
            r"\.candidate-card\s*\{[^}]*height:\s*84px",
        )
        self.assertIn("shouldRenderDailyBias", self.js)
        self.assertIn("removeContextRiskDuplicate", self.js)

    def test_risk_badges_keep_positive_metric_gap_and_skip_empty_label(self) -> None:
        self.assertNotIn('labels.push("无额外风险")', self.js)
        self.assertIn("candidate-tags", self.js)
        self.assertIn("candidate-tag-row--flags", self.js)
        self.assertIn("info-tag--flag", self.js)
        self.assertIn("flex-direction: column", self.css)
        self.assertNotIn("margin: -2px 0 12px", self.css)
        self.assertNotIn(".risk-badges", self.css)

    def test_scan_status_renders_backend_times_and_bootstraps_running_state(self) -> None:
        self.assertIn("function formatBackendEventTime", self.js)
        self.assertIn("snapshot.started_at", self.js)
        self.assertIn("snapshot.finished_at", self.js)
        self.assertIn('timeZone: "Asia/Shanghai"', self.js)
        self.assertIn('snapshot.error || "扫描失败，已保留现有报告卡片。"', self.js)
        self.assertIn('state === "idle" &&', self.js)
        self.assertIn("startPolling();", self.js)

    def test_dashboard_explains_crypto_bitget_filter_and_unavailable_links(self) -> None:
        self.assertIn("instCategory=1", self.index_html)
        self.assertIn("Bitget 官方 USDT 永续交集", self.index_html)
        self.assertIn("不限制上市天数", self.index_html)
        self.assertNotIn("上市时间与数据完整", self.index_html)
        self.assertIn("稳定币基准排除", self.index_html)
        self.assertIn("if (bitgetUrl)", self.js)
        self.assertNotIn("Bitget 暂无对应合约", self.js)

    def test_summary_copy_is_explicit_about_time_zone_and_observation_boundary(self) -> None:
        self.assertIn("北京时间 UTC+8", self.index_html)
        self.assertIn("观察，不是开仓信号", self.index_html)
        self.assertIn("重点看", self.index_html)
        self.assertIn("涨跌幅榜", self.index_html)
        self.assertIn("连续涨跌 / 放量", self.index_html)
        self.assertLess(
            self.index_html.index('id="movers-section"'),
            self.index_html.index('id="momentum-section"'),
        )
        self.assertLess(
            self.index_html.index('id="momentum-section"'),
            self.index_html.index('id="featured-section"'),
        )
        self.assertIn(">阶段<", self.index_html)
        self.assertIn("brand-mark", self.index_html)
        self.assertNotIn("观线", self.index_html)
        self.assertNotIn("SIX / STAGE", self.index_html)
        self.assertNotIn("接近下一阶段", self.index_html)
        self.assertNotIn("near-stage", self.index_html)
        self.assertIn("北京时间 UTC+8", self.js)

    def test_risk_tag_requires_closed_candle_and_no_auto_order_notice(self) -> None:
        self.assertIn("仅使用已收盘K线", self.index_html)
        self.assertIn("仅作筛选", self.index_html)
        self.assertIn("不提供投资建议", self.index_html)
        self.assertIn("不自动下单", self.index_html)
        self.assertIn('class="disclaimer"', self.index_html)
        self.assertLess(
            self.index_html.index('class="disclaimer"'),
            self.index_html.index("</header>"),
        )

    def test_polling_retries_transient_errors_and_enables_button_after_reload(self) -> None:
        self.assertIn("MAX_POLL_ERRORS", self.js)
        self.assertIn("pollErrorCount", self.js)
        self.assertRegex(
            self.js,
            r"if \(pollErrorCount <= MAX_POLL_ERRORS\)[\s\S]*?return;",
        )
        self.assertRegex(
            self.js,
            r"await loadDashboard\(\);\s*get\(\"scan-now\"\)\.disabled = false;",
        )

    def test_polling_recovery_hides_transient_error_on_running_or_success(self) -> None:
        self.assertRegex(
            self.js,
            r'if \(snapshot\.state === "running" \|\| snapshot\.state === "succeeded"\) \{\s*hideError\(\);',
        )

    def test_compact_cards_render_readiness_and_chinese_display_fields(self) -> None:
        """Cards consume Task 1's display-safe fields and keep only compact facts."""
        for field in (
            "entry_readiness",
            "entry_readiness_label",
            "stage_label",
            "direction_label",
            "fifteen_minute_state_label",
            "four_hour_context_label",
            "daily_bias_label",
            "risk_labels_display",
            "change_24h_pct",
        ):
            self.assertIn(f"item.{field}", self.js)

        for label in (
            "入场准备度",
        ):
            self.assertIn(label, self.js)

        card_source = self.js.split("function renderCard", 1)[1].split(
            "function renderStage", 1
        )[0]
        for obsolete in (
            "当前压缩 ATR",
            "压缩比例",
            "连续压缩 K 线",
            "收缩比",
            "当前收盘相对六线",
            "15m 区间低",
            "15m 区间高",
            "4H 区间低",
            "4H 区间高",
            "15m 窗口末端",
            "4H 窗口末端",
            "item.fifteen_minute_current_compression_atr",
            "item.fifteen_minute_compressed_fraction",
            "item.fifteen_minute_trailing_compressed_bars",
            "item.fifteen_minute_contraction_ratio",
            "扫描价",
            "15m 状态",
            "15m 密集分",
            "4H 环境",
            "日线方向",
        ):
            self.assertNotIn(obsolete, card_source)

        self.assertIn("item.risk_labels_display", self.js)
        self.assertIn('appendChange(main, item, "candidate-change")', card_source)
        self.assertNotIn("directionLabel(item.direction)", card_source)
        self.assertNotIn("item.four_hour_context || item.four_hour_state", card_source)

    def test_compact_cards_use_three_two_one_column_breakpoints(self) -> None:
        """Each stage's card list is three columns, then two, then one."""
        self.assertRegex(
            self.css,
            r"\.card-list\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)",
        )
        medium = self.css.split("@media (max-width: 1180px)", 1)[1].split(
            "@media (max-width: 760px)", 1
        )[0]
        self.assertRegex(
            medium,
            r"\.card-list\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)",
        )
        mobile = self.css.split("@media (max-width: 760px)", 1)[1]
        self.assertRegex(
            mobile,
            r"\.card-list\s*\{[^}]*grid-template-columns:\s*1fr",
        )
        self.assertRegex(self.css, r"\.candidate-card\s*\{[^}]*height:\s*84px")
        self.assertRegex(self.css, r"\.candidate-card\s*\{[^}]*min-height:\s*84px")

    def test_stage_panels_are_full_width_before_card_columns_apply(self) -> None:
        """At desktop widths each stage spans the page; cards own the 3/2/1 grid."""
        desktop = self.css.split("@media (max-width: 760px)", 1)[0]
        self.assertRegex(
            desktop,
            r"\.stage-grid\s*\{[^}]*grid-template-columns:\s*1fr",
        )
        self.assertRegex(
            desktop,
            r"\.stage-panel\s*\{[^}]*grid-column:\s*1",
        )

    def test_page_explains_entry_readiness_boundary(self) -> None:
        self.assertIn("入场准备度", self.js)
        self.assertNotIn(
            "入场准备度仅表示筛选条件完成程度，不代表盈利概率、胜率或收益预测。",
            self.index_html,
        )

    def test_ready_report_with_zero_candidates_shows_clear_empty_state(self) -> None:
        """A successful scan with no opportunities must not leave a blank page."""
        self.assertIn('heading.textContent = "当前没有符合条件的合约"', self.js)
        ready_branch = self.js.split('if (summary.status === "ready") {', 1)[1].split(
            "return;", 1
        )[0]
        self.assertIn("empty.hidden = candidateCount > 0 || hasMovers(data);", ready_branch)

    def test_real_dashboard_script_renders_cards_and_refreshes_stage_visibility(self) -> None:
        """Load dashboard.js in Node and inspect cards plus empty-stage refresh behavior."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for the real dashboard DOM contract")

        harness = r'''
const fs = require("fs");
const vm = require("vm");

class Element {
  constructor(tagName) {
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this._text = "";
    this.className = "";
    this._hidden = false;
    this.style = {};
    this.listeners = {};
    this.id = "";
    this.value = "";
    this._href = "";
    this.classList = {
      add: (...names) => names.forEach((name) => this._toggleClass(name, true)),
      remove: (...names) => names.forEach((name) => this._toggleClass(name, false)),
      toggle: (name, force) => {
        const next = force === undefined ? !this.classList.contains(name) : force;
        this._toggleClass(name, next);
        return next;
      },
      contains: (name) => this.className.split(/\s+/).filter(Boolean).includes(name),
    };
  }

  _toggleClass(name, enabled) {
    const names = new Set(this.className.split(/\s+/).filter(Boolean));
    if (enabled) names.add(name); else names.delete(name);
    this.className = [...names].join(" ");
  }

  set textContent(value) {
    this._text = value === null || value === undefined ? "" : String(value);
    this.children = [];
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }

  set hidden(value) {
    this._hidden = Boolean(value);
    if (this.getAttribute("data-stage") === "entry_confirmed") {
      entryHiddenStates.push(this._hidden);
    }
  }

  get hidden() { return this._hidden; }

  set href(value) { this._href = String(value); }

  get href() { return this._href; }

  append(...nodes) {
    nodes.forEach((node) => {
      if (!node) return;
      this.children.push(node);
      node.parentNode = this;
    });
  }

  appendChild(node) {
    this.append(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name === "href") this.href = value;
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }

  matches(selector) {
    const attr = selector.match(/^\[([^=\]]+)(?:=["']?([^\]"']+)["']?)?\]$/);
    if (attr) {
      return this.getAttribute(attr[1]) !== null
        && (attr[2] === undefined || this.getAttribute(attr[1]) === attr[2]);
    }
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
    return this.tagName.toLowerCase() === selector.toLowerCase();
  }

  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/).filter(Boolean);
    const descendants = [];
    const visit = (node) => {
      node.children.forEach((child) => {
        descendants.push(child);
        visit(child);
      });
    };
    visit(this);
    if (parts.length === 1) return descendants.filter((node) => node.matches(parts[0]));
    return descendants.filter((node) => {
      if (!node.matches(parts[parts.length - 1])) return false;
      let ancestor = node.parentNode;
      for (let index = parts.length - 2; index >= 0; index -= 1) {
        while (ancestor && !ancestor.matches(parts[index])) ancestor = ancestor.parentNode;
        if (!ancestor) return false;
        ancestor = ancestor.parentNode;
      }
      return true;
    });
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

class Document extends Element {
  constructor() {
    super("document");
    this.readyState = "loading";
  }

  createElement(tagName) { return new Element(tagName); }

  getElementById(id) { return this.querySelector(`#${id}`); }

  dispatchEvent(name) {
    if (this.listeners[name]) this.listeners[name]({ type: name });
  }
}

const document = new Document();
const window = {
  setInterval: () => 1,
  clearInterval: () => {},
};
window.window = window;
const entryHiddenStates = [];

function mount(tagName, attributes = {}, parent = document) {
  const node = document.createElement(tagName);
  Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
  parent.append(node);
  return node;
}

mount("time", { id: "beijing-clock" });
mount("button", { id: "scan-now" });
mount("p", { id: "scan-state" });
mount("time", { id: "scan-started-at" });
mount("time", { id: "scan-finished-at" });
mount("span", { id: "summary-status" });
mount("span", { id: "summary-retrieved" });
mount("span", { id: "summary-live" });
mount("span", { id: "summary-eligible" });
mount("span", { id: "summary-candidates" });
mount("span", { id: "summary-errors" });
const empty = mount("section", { id: "empty-state" });
mount("h2", {}, empty);
mount("p", {}, empty);
const error = mount("section", { id: "error-state" });
mount("p", { id: "error-message" }, error);
mount("section", { id: "scan-progress" });
mount("span", { id: "progress-stage-label" });
for (const name of ["daily", "four-hour", "fifteen-minute"]) {
  mount("div", { id: `progress-${name}` });
}
for (const stage of ["entry_confirmed", "wait_first_pullback", "mature_15m_coil", "forming_15m_coil"]) {
  const panel = mount("section", { "data-stage": stage });
  mount("span", { "data-stage-count": "" }, panel);
  mount("div", { "data-stage-cards": "" }, panel);
}
mount("section", { id: "featured-section" });
mount("span", { id: "featured-count" });
mount("div", { id: "featured-cards" });
mount("section", { id: "movers-section" });
mount("span", { id: "gainers-count" });
mount("span", { id: "losers-count" });
mount("div", { id: "gainers-cards" });
mount("div", { id: "losers-cards" });
mount("section", { id: "momentum-section" });
mount("span", { id: "momentum-launched-count" });
mount("span", { id: "momentum-watch-count" });
mount("div", { id: "momentum-launched-cards" });
mount("div", { id: "momentum-watch-cards" });

const payload = {
  summary: { status: "ready", retrieved_at_shanghai: "2026-08-12 08:30", live_usdt_swaps: 3, eligible_trade_contracts: 2, error_count: 0 },
  scan: { state: "idle" },
  stages: {
    entry_confirmed: [{
      instrument: "FORMAL-USDT-SWAP",
      stage: "entry_confirmed",
      stage_label: "回踩确认",
      direction: "long",
      direction_label: "做多",
      entry_readiness: 88,
      entry_readiness_label: "准备度高",
      fifteen_minute_state: "breakout_long",
      fifteen_minute_state_label: "向上突破",
      fifteen_minute_state_quality: 92,
      fifteen_minute_coil_score: 11,
      four_hour_context: "opposite",
      four_hour_context_label: "高周期反向",
      daily_bias: "short",
      daily_bias_label: "做空",
      risk_labels: ["opposite", "expanded_risk"],
      risk_labels_display: ["高周期方向相反", "均线已经发散"],
      reason: "first_pullback_held",
      latest_price: 123.456,
      change_24h_pct: 3.25,
      countertrend_signal: "topping_observation",
      countertrend_signal_label: "摸顶观察",
      countertrend_signal_detail: "15m逆势冲高，等转弱确认",
      fifteen_minute_breakout_at: "2026-08-12 01:00",
      four_hour_breakout_at: "2026-08-12 00:00",
      first_pullback_at: "2026-08-12 01:15",
      next_executable_at: "2026-08-12 01:30",
      initial_stop_price: 123.456,
      fifteen_minute_current_compression_atr: 1.234,
      fifteen_minute_compressed_fraction: 0.42,
      fifteen_minute_trailing_compressed_bars: 11,
      fifteen_minute_contraction_ratio: 0.18,
      fifteen_minute_zone_low: 120,
      fifteen_minute_zone_high: 125,
      four_hour_zone_low: 110,
      four_hour_zone_high: 130,
      fifteen_minute_window_end: "2026-08-12 02:00",
      four_hour_window_end: "2026-08-12 04:00",
      bitget_url: "https://www.bitget.com/zh-CN/futures/usdt/FORMALUSDT",
    }],
    wait_first_pullback: [],
    mature_15m_coil: [{
      instrument: "OBSERVATION-USDT-SWAP",
      stage: "mature_15m_coil",
      stage_label: "15m 成熟密集",
      direction: "long",
      direction_label: "做多",
      entry_readiness: 55,
      entry_readiness_label: "仅观察",
      fifteen_minute_state: "mature_coil",
      fifteen_minute_state_label: "成熟密集",
      fifteen_minute_state_quality: 77,
      fifteen_minute_coil_score: 77,
      four_hour_context: "trend_aligned",
      four_hour_context_label: "高周期同向",
      daily_bias: "long",
      daily_bias_label: "做多",
      risk_labels: ["daily_trend_only"],
      risk_labels_display: [],
      reason: "fifteen_minute_coil_ready",
      change_24h_pct: -1.5,
      fifteen_minute_breakout_at: "2026-08-12 03:00",
      four_hour_breakout_at: "2026-08-12 00:00",
      initial_stop_price: 222.222,
      fifteen_minute_window_end: "2026-08-12 04:00",
      four_hour_window_end: "2026-08-12 08:00",
      bitget_url: "https://www.bitget.com/zh-CN/futures/usdt/OBSERVATIONUSDT",
    }],
    forming_15m_coil: [],
  },
  movers: {
    gainers: [
      { instrument: "UP-USDT-SWAP", change_24h_pct: 9.5, latest_price: 12, bitget_url: "https://www.bitget.com/zh-CN/futures/usdt/UPUSDT" },
    ],
    losers: [
      { instrument: "DOWN-USDT-SWAP", change_24h_pct: -8.4, latest_price: 1.1, bitget_url: "https://www.bitget.com/zh-CN/futures/usdt/DOWNUSDT" },
    ],
  },
  momentum: {
    launched: [
      { instrument: "SNDK-USDT-SWAP", change_24h_pct: 6.1, latest_price: 1643.92, status_label: "连续上涨且放量", daily_streak: 3, volume_ratio: 2.2, bitget_url: "https://www.bitget.com/zh-CN/futures/usdt/SNDKUSDT" },
    ],
    watch: [],
  },
  featured: [
    {
      instrument: "ODDS-USDT-SWAP",
      stage: "wait_first_pullback",
      stage_label: "等待首次回踩",
      direction: "short",
      direction_label: "做空",
      entry_readiness: 82,
      entry_readiness_label: "接近执行",
      playbook: "pullback_20",
      playbook_label: "回踩20",
      planned_rr: 3.2,
      odds_ok: true,
      target_price: 1.11,
      fifteen_minute_state_label: "向下突破",
      fifteen_minute_state_quality: 90,
      four_hour_context: "trend_aligned",
      four_hour_context_label: "高周期同向",
      daily_bias_label: "做空",
      risk_labels_display: [],
      reason: "fifteen_minute_breakout_waiting_for_pullback",
      latest_price: 1.25,
      change_24h_pct: 2.1,
      initial_stop_price: 1.28,
      bitget_url: "https://www.bitget.com/zh-CN/futures/usdt/ODDSUSDT",
    },
  ],
};

const refreshedPayload = JSON.parse(JSON.stringify(payload));
payload.scan.state = "running";
payload.stages.entry_confirmed = [];
let dashboardReads = 0;

const context = {
  console,
  document,
  window,
  fetch: (url) => {
    if (url === "/api/scan/status") {
      return Promise.resolve({ ok: true, json: async () => ({ state: "succeeded" }) });
    }
    dashboardReads += 1;
    const response = dashboardReads === 1 ? payload : refreshedPayload;
    return Promise.resolve({ ok: true, json: async () => response });
  },
  setTimeout,
  clearTimeout,
  Intl,
  Date,
};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), context, { filename: process.argv[1] });
document.dispatchEvent("DOMContentLoaded");

setTimeout(() => {
  const panel = (stage) => document.querySelector(`[data-stage="${stage}"]`);
  const card = (stage) => panel(stage).querySelector(".candidate-card");
  const details = (node) => ({
    text: node.textContent,
    href: node.querySelector(".bitget-button").href,
    symbol: node.querySelector(".candidate-symbol").textContent,
    price: node.querySelector(".candidate-price").textContent,
    change: node.querySelector(".candidate-change").textContent,
    changeClass: node.querySelector(".candidate-change").className,
    tags: node.querySelector(".candidate-tags").textContent,
    readinessNow: node.querySelector(".readiness-value").getAttribute("aria-valuenow"),
    readinessText: node.querySelector(".readiness-value").textContent,
    readinessLabel: node.querySelector(".readiness-value").getAttribute("aria-label"),
  });
  console.log(JSON.stringify({
    formal: details(card("entry_confirmed")),
    observation: details(card("mature_15m_coil")),
    featured: details(document.getElementById("featured-cards").querySelector(".candidate-card")),
    featuredCount: document.getElementById("featured-count").textContent,
    featuredHidden: document.getElementById("featured-section").hidden,
    featuredInstruments: document.getElementById("featured-cards").querySelectorAll(".candidate-symbol").map((item) => item.textContent),
    moversHidden: document.getElementById("movers-section").hidden,
    gainerText: document.getElementById("gainers-cards").querySelector(".mover-card").textContent,
    gainerClass: document.getElementById("gainers-cards").querySelector(".mover-change").className,
    momentumHidden: document.getElementById("momentum-section").hidden,
    momentumText: document.getElementById("momentum-launched-cards").querySelector(".mover-card").textContent,
    loserText: document.getElementById("losers-cards").querySelector(".mover-card").textContent,
    loserClass: document.getElementById("losers-cards").querySelector(".mover-change").className,
    entryHiddenStates,
    entryPanelHidden: panel("entry_confirmed").hidden,
    observationPanelHidden: panel("mature_15m_coil").hidden,
  }));
}, 10);
'''
        completed = subprocess.run(
            [
                node,
                "-e",
                harness,
                str(Path(__file__).resolve().parents[1] / "dashboard/static/dashboard.js"),
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        formal = payload["formal"]
        observation = payload["observation"]

        self.assertGreaterEqual(len(payload["entryHiddenStates"]), 2)
        self.assertTrue(payload["entryHiddenStates"][0])
        self.assertFalse(payload["entryHiddenStates"][-1])
        self.assertFalse(payload["entryPanelHidden"])
        self.assertFalse(payload["observationPanelHidden"])
        self.assertEqual(payload["featuredCount"], "1")
        self.assertFalse(payload["featuredHidden"])
        self.assertEqual(
            payload["featuredInstruments"],
            ["ODDS"],
        )
        featured = payload["featured"]
        self.assertEqual(featured["symbol"], "ODDS")
        self.assertEqual(featured["readinessText"], "3.2R")
        self.assertIn("计划赔率 3.2R", featured["readinessLabel"])
        self.assertIn("🎯回踩20", featured["tags"])
        self.assertIn("TP 1.11", featured["tags"])
        self.assertFalse(payload["moversHidden"])
        self.assertFalse(payload["momentumHidden"])
        self.assertIn("UP", payload["gainerText"])
        self.assertIn("12", payload["gainerText"])
        self.assertIn("+9.50%", payload["gainerText"])
        self.assertIn("is-up", payload["gainerClass"])
        self.assertIn("DOWN", payload["loserText"])
        self.assertIn("1.1", payload["loserText"])
        self.assertIn("-8.40%", payload["loserText"])
        self.assertIn("SNDK", payload["momentumText"])
        self.assertIn("1643.92", payload["momentumText"])
        self.assertIn("连续上涨且放量", payload["momentumText"])
        self.assertIn("is-down", payload["loserClass"])

        self.assertEqual(formal["symbol"], "FORMAL")
        self.assertEqual(observation["symbol"], "OBSERVATION")
        self.assertEqual(formal["readinessNow"], "88")
        self.assertEqual(formal["readinessText"], "88")
        self.assertIn("入场准备度 88", formal["readinessLabel"])
        self.assertNotIn("🟢多", formal["text"])
        self.assertNotIn("🔴空", formal["text"])
        self.assertNotIn("⚪待定", formal["text"])
        self.assertNotIn("🟢", formal["text"])
        self.assertNotIn("🔴", formal["text"])
        self.assertIn("✅回踩确认", formal["tags"])
        self.assertEqual(formal["price"], "123.456")
        self.assertEqual(formal["change"], "+3.25%")
        self.assertIn("is-up", formal["changeClass"])
        self.assertEqual(observation["change"], "-1.50%")
        self.assertIn("is-down", observation["changeClass"])
        self.assertEqual(featured["change"], "+2.10%")
        self.assertIn("is-up", featured["changeClass"])
        self.assertIn("15m 92", formal["tags"])
        self.assertIn("📉4H反向", formal["tags"])
        self.assertIn("日线空", formal["tags"])
        self.assertIn("SL 123.456", formal["tags"])
        self.assertIn("🔺摸顶", formal["tags"])
        self.assertIn("⚠发散", formal["tags"])
        self.assertNotIn("扫描价", formal["text"])
        self.assertNotIn("15m逆势冲高，等转弱确认", formal["text"])
        self.assertNotIn("高周期方向相反", formal["text"])
        self.assertEqual(
            formal["href"],
            "https://www.bitget.com/zh-CN/futures/usdt/FORMALUSDT",
        )
        self.assertIn("🌀成熟密集", observation["tags"])
        self.assertIn("15m 77", observation["tags"])
        self.assertIn("📈4H同向", observation["tags"])
        self.assertNotIn("SL", observation["tags"])
        self.assertNotIn("仅观察，不是开仓信号", observation["text"])
        self.assertEqual(
            observation["href"],
            "https://www.bitget.com/zh-CN/futures/usdt/OBSERVATIONUSDT",
        )
        for card in (formal, observation):
            for obsolete in (
                "当前压缩 ATR",
                "压缩比例",
                "连续压缩 K 线",
                "收缩比",
                "15m 区间低",
                "15m 区间高",
                "4H 区间低",
                "4H 区间高",
                "15m 窗口末端",
                "4H 窗口末端",
                "fifteen_minute_current_compression_atr",
                "fifteen_minute_contraction_ratio",
                "entry_confirmed",
                "breakout_long",
                "opposite",
                "daily_trend_only",
            ):
                self.assertNotIn(obsolete, card["text"])

    def test_removed_frontend_symbols_are_not_kept_as_dead_code(self) -> None:
        self.assertNotIn("FORMAL_STAGES", self.js)
        self.assertNotIn(".candidate-reason", self.css)

    def test_featured_section_is_rendered_ahead_of_stage_categories(self) -> None:
        self.assertIn("function renderFeatured", self.js)
        self.assertIn("data.featured", self.js)
        self.assertIn("function renderMovers", self.js)
        self.assertIn("mover-price", self.js)
        self.assertIn("data.movers", self.js)
        self.assertIn('id="featured-section"', self.index_html)
        self.assertIn('id="featured-cards"', self.index_html)
        self.assertIn('id="movers-section"', self.index_html)
        self.assertRegex(
            self.css,
            r"\.featured-panel\s*\{[^}]*border:\s*1px solid rgba\(71, 215, 232, 0.42\)",
        )
        self.assertRegex(self.css, r"\.mover-list\s*\{[^}]*repeat\(3,")
        self.assertRegex(self.css, r"\.mover-list--wide\s*\{[^}]*repeat\(3,")
        self.assertRegex(self.css, r"\.mover-card--stack\s*\{[^}]*justify-content:\s*center")
        self.assertRegex(
            self.css,
            r"\.mover-card--stack \.mover-price\s*,[^}]*font-size:\s*14px",
        )
        self.assertRegex(
            self.css,
            r"\.mover-row\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto minmax\(0, 1fr\)",
        )
        self.assertRegex(self.css, r"\.mover-change\.is-up\s*\{[^}]*color:\s*var\(--green\)")
        self.assertRegex(self.css, r"\.mover-change\.is-down\s*\{[^}]*color:\s*var\(--danger\)")
        self.assertRegex(self.css, r"\.candidate-change\.is-up\s*\{[^}]*color:\s*var\(--green\)")
        self.assertRegex(self.css, r"\.candidate-change\.is-down\s*\{[^}]*color:\s*var\(--danger\)")

    def test_template_uses_chinese_stage_index_labels(self) -> None:
        for obsolete in ("CONFIRMED", "PULLBACK", "MATURE 15M", "FORMING 15M"):
            self.assertNotIn(obsolete, self.index_html)
        for label in ("回踩确认", "等待第一次回踩", "成熟密集（观察）", "正在形成密集（观察）", "重点看", "涨跌幅榜"):
            self.assertIn(label, self.index_html)


def write_report_fixture(
    report_dir: Path,
    rows: list[dict[str, object]],
    *,
    default_fifteen_minute_score: object = 1,
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    normalized_rows = []
    for row in rows:
        normalized = dict(row)
        if (
            normalized.get("stage")
            in {
                "entry_confirmed",
                "wait_first_pullback",
                "mature_15m_coil",
                "forming_15m_coil",
            }
            and "fifteen_minute_coil_score" not in normalized
            and default_fifteen_minute_score is not None
        ):
            normalized["fifteen_minute_coil_score"] = default_fifteen_minute_score
        normalized_rows.append(normalized)
    frame = pd.DataFrame(normalized_rows)
    frame.to_csv(report_dir / "latest.csv", index=False)
    (report_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "retrievedAt": "2026-08-12T00:30:00+00:00",
                "liveUsdtSwaps": 3,
                "eligibleTradeContracts": 2,
                "errors": {"ERROR-USDT-SWAP": "fixture error"},
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
