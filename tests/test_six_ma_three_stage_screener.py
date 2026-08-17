from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from user_data.strategy_lib.six_ma_three_stage_screener import (
    ThreeStageParameters,
    build_odds_plan,
    classify_four_hour_context,
    collect_coil_zones,
    scan_three_stage_pair,
)


UTC = "UTC"
SIX = ("ma20", "ma60", "ma120", "ema20", "ema60", "ema120")


class SixMaThreeStageScreenerTests(unittest.TestCase):
    def test_short_pullback_far_from_ma20_band_does_not_touch(self) -> None:
        far = candle_frame(
            "2025-01-03 08:15", close=99.0, high=99.2, low=98.8
        )
        far.loc[:, ["ma20", "ema20"]] = [100.0, 100.2]
        candles15m = pd.concat(
            [coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
             candle_frame("2025-01-03 08:00", close=99.0), far],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("short"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )

        self.assertEqual(result["stage"], "wait_first_pullback")

    def test_short_pullback_true_ma20_band_touch_confirms(self) -> None:
        touch = candle_frame(
            "2025-01-03 08:15", close=99.4, high=100.0, low=99.2
        )
        touch.loc[:, ["ma20", "ema20"]] = [100.0, 100.2]
        candles15m = pd.concat(
            [coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
             candle_frame("2025-01-03 08:00", close=99.0), touch],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("short"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )

        self.assertEqual(result["stage"], "entry_confirmed")

    def test_invalid_local_pullback_cannot_revive_mature_tail(self) -> None:
        failed = candle_frame(
            "2025-01-03 08:15", close=100.6, high=100.8, low=99.0
        )
        candles15m = pd.concat(
            [coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
             candle_frame("2025-01-03 08:00", close=101.0), failed],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=pd.DataFrame(),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["fifteen_minute_coil_score"], 0.0)
        self.assertEqual(result["reason"], "first_pullback_failed")

    def test_expired_local_pullback_cannot_revive_mature_tail(self) -> None:
        waiting = [
            candle_frame(
                (pd.Timestamp("2025-01-03 08:15", tz=UTC)
                 + pd.Timedelta(minutes=15 * offset)).isoformat(),
                close=102.0,
                high=102.2,
                low=101.8,
            )
            for offset in range(13)
        ]
        candles15m = pd.concat(
            [coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
             candle_frame("2025-01-03 08:00", close=101.0), *waiting],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=pd.DataFrame(),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 11:30", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["fifteen_minute_coil_score"], 0.0)
        self.assertEqual(result["reason"], "first_pullback_expired")

    def test_short_prefilled_history_cannot_bypass_15m_raw_bar_gate(self) -> None:
        result = scan_pair(
            candles4h=pd.DataFrame(),
            candles15m=layered_frame(raw_bars=120, mode="mature"),
            as_of=pd.Timestamp("2025-01-02 05:45", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["reason"], "insufficient_15m_data")

    def test_prefilled_134_bars_cannot_bypass_mature_15m_gate(self) -> None:
        result = scan_pair(
            candles4h=pd.DataFrame(),
            candles15m=layered_frame(raw_bars=134, mode="mature"),
            as_of=pd.Timestamp("2025-01-02 09:30", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertNotEqual(result["stage"], "mature_15m_coil")

    def test_short_prefilled_4h_and_daily_context_is_unknown(self) -> None:
        result = scan_pair(
            candles4h=ordered_frame("2025-01-01", periods=13, freq="4h", side="long"),
            candles15m=layered_frame(raw_bars=135, mode="mature"),
            candles1d=ordered_frame("2025-01-01", periods=10, freq="1d", side="long"),
            as_of=pd.Timestamp("2025-01-02 09:45", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertEqual(result["four_hour_context"], "unknown")
        self.assertEqual(result["daily_direction"], "unknown")

    def test_short_prefilled_4h_breakout_cannot_arm_formal_setup(self) -> None:
        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=layered_frame(raw_bars=135, mode="mature"),
            as_of=pd.Timestamp("2025-01-03 08:00", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertTrue(pd.isna(result["four_hour_breakout_at"]))
        self.assertEqual(result["four_hour_context"], "unknown")

    def test_malformed_higher_timeframes_fail_open_with_risk_labels(self) -> None:
        malformed = pd.DataFrame({"date": ["not-a-date"], "close": [100.0]})
        result = scan_pair(
            candles4h=malformed,
            candles15m=layered_frame(raw_bars=135, mode="mature"),
            candles1d=malformed,
            as_of=pd.Timestamp("2025-01-02 09:45", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertEqual(result["four_hour_context"], "unknown")
        self.assertIn("four_hour_unknown", result["risk_labels"])
        self.assertIn("daily_unknown", result["risk_labels"])

    def test_six_four_hour_context_states_and_trend_alignment(self) -> None:
        daily_long = ordered_frame("2025-01-01", periods=4, freq="1d", side="long")
        daily_short = ordered_frame("2025-01-01", periods=4, freq="1d", side="short")
        forming = pd.concat(
            [
                coil_frame("2025-01-01", periods=4, freq="4h", spread=4.0),
                coil_frame("2025-01-01 16:00", periods=4, freq="4h", spread=1.0),
            ],
            ignore_index=True,
        )
        neutral = pd.concat(
            [
                candle_frame(
                    date.isoformat(), close=100.0, spread=3.0
                )
                for date in pd.date_range(
                    "2025-01-01", periods=13, freq="4h", tz=UTC
                )
            ],
            ignore_index=True,
        )
        cases = {
            "mature_coil": (coil_frame("2025-01-01", periods=12, freq="4h"), daily_long, "neutral"),
            "forming_coil": (forming, daily_long, "neutral"),
            "trend_aligned": (ordered_frame("2025-01-01", periods=13, freq="4h", side="long"), daily_long, "long"),
            "daily_trend_only": (neutral, daily_long, "neutral"),
            "unknown": (pd.DataFrame(), pd.DataFrame(), "neutral"),
            "opposite": (ordered_frame("2025-01-01", periods=13, freq="4h", side="short"), daily_short, "long"),
        }
        for expected, (four_hour, daily, lower_direction) in cases.items():
            with self.subTest(expected=expected):
                raw_4h = {
                    "mature_coil": 131,
                    "forming_coil": 127,
                    "trend_aligned": 131,
                    "daily_trend_only": 131,
                    "unknown": 0,
                    "opposite": 131,
                }[expected]
                raw_daily = 63 if expected != "unknown" else 0
                context = classify_four_hour_context(
                    four_hour,
                    daily,
                    lower_direction,
                    four_hour_raw_bars=raw_4h,
                    daily_raw_bars=raw_daily,
                )
                self.assertEqual(context["state"], expected)
        not_aligned = classify_four_hour_context(
            ordered_frame("2025-01-01", periods=13, freq="4h", side="long"),
            pd.DataFrame(),
            "neutral",
            four_hour_raw_bars=131,
            daily_raw_bars=0,
        )
        self.assertNotEqual(not_aligned["state"], "trend_aligned")

    def test_127_raw_bars_can_form_but_not_mature(self) -> None:
        result = scan_pair(
            candles4h=coil_frame("2025-01-01", periods=12, freq="4h"),
            candles15m=layered_frame(raw_bars=127, mode="forming"),
            as_of=pd.Timestamp("2025-01-02 07:45", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertEqual(result["stage"], "forming_15m_coil")
        self.assertEqual(result["fifteen_minute_state"], "forming_coil")
        self.assertGreater(float(result["fifteen_minute_contraction_ratio"]), 0.20)

    def test_135_raw_bars_can_emit_mature_15m_coil(self) -> None:
        result = scan_pair(
            candles4h=coil_frame("2025-01-01", periods=12, freq="4h"),
            candles15m=layered_frame(raw_bars=135, mode="mature"),
            as_of=pd.Timestamp("2025-01-02 09:45", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertEqual(result["fifteen_minute_state"], "mature_coil")
        self.assertGreater(float(result["fifteen_minute_coil_score"]), 0.0)

    def test_score_zero_expired_and_gapped_windows_never_emit_observation(self) -> None:
        candles15m = layered_frame(raw_bars=127, mode="forming").drop(index=123)
        result = scan_pair(
            candles4h=coil_frame("2025-01-01", periods=12, freq="4h"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-02 07:45", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(float(result["fifteen_minute_coil_score"]), 0.0)
    def test_current_4h_and_15m_coils_are_watch_only(self) -> None:
        candles4h = coil_frame("2025-01-01", periods=12, freq="4h")
        candles15m = coil_frame("2025-01-02 20:00", periods=16, freq="15min")

        result = scan_pair(
            candles4h=candles4h,
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 00:00", tz=UTC),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertEqual(result["direction"], "short")
        self.assertEqual(result["four_hour_state"], "mature_coil")
        self.assertEqual(result["fifteen_minute_state"], "mature_coil")
        self.assertTrue(np.isnan(float(result["initial_stop_price"])))
        self.assertTrue(pd.isna(result["next_executable_at"]))

    def test_4h_breakout_then_fresh_15m_coil_is_ready(self) -> None:
        candles4h = higher_breakout_frame("long")
        candles15m = coil_frame("2025-01-03 04:00", periods=16, freq="15min")

        result = scan_pair(
            candles4h=candles4h,
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:00", tz=UTC),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertEqual(result["direction"], "long")
        self.assertEqual(result["four_hour_state"], "breakout_long")
        self.assertEqual(
            result["four_hour_breakout_at"],
            pd.Timestamp("2025-01-03 04:00", tz=UTC),
        )
        self.assertEqual(result["fifteen_minute_state"], "mature_coil")

    def test_plain_4h_trend_context_preserves_a_15m_observation(self) -> None:
        candles4h = ordered_frame("2025-01-01", periods=13, freq="4h", side="long")
        candles15m = coil_frame("2025-01-03 04:00", periods=16, freq="15min")

        result = scan_pair(
            candles4h=candles4h,
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:00", tz=UTC),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertEqual(result["four_hour_context"], "opposite")

    def test_15m_breakout_waits_for_a_later_first_pullback(self) -> None:
        candles15m = pd.concat(
            [
                coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
                candle_frame("2025-01-03 08:00", close=101.0),
            ],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:15", tz=UTC),
        )

        self.assertEqual(result["stage"], "wait_first_pullback")
        self.assertEqual(result["direction"], "long")
        self.assertEqual(result["fifteen_minute_state"], "breakout_long")
        self.assertEqual(
            result["fifteen_minute_breakout_at"],
            pd.Timestamp("2025-01-03 08:15", tz=UTC),
        )

    def test_first_pullback_hold_confirms_next_open_and_far_side_stop(self) -> None:
        candles15m = pd.concat(
            [
                coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
                candle_frame("2025-01-03 08:00", close=101.0),
                candle_frame(
                    "2025-01-03 08:15",
                    close=100.6,
                    high=100.8,
                    low=100.4,
                ),
            ],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )

        self.assertEqual(result["stage"], "entry_confirmed")
        self.assertEqual(
            result["first_pullback_at"],
            pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )
        self.assertEqual(
            result["next_executable_at"],
            pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )
        self.assertAlmostEqual(float(result["initial_stop_price"]), 99.1)

    def test_failed_first_touch_cannot_be_revived_by_a_second_touch(self) -> None:
        candles15m = pd.concat(
            [
                coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
                candle_frame("2025-01-03 08:00", close=101.0),
                candle_frame(
                    "2025-01-03 08:15",
                    close=100.6,
                    high=100.8,
                    low=99.0,
                ),
                candle_frame(
                    "2025-01-03 08:30",
                    close=100.7,
                    high=100.9,
                    low=100.4,
                ),
            ],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:45", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["reason"], "first_pullback_failed")

    def test_short_breakout_and_pullback_are_the_long_mirror(self) -> None:
        candles15m = pd.concat(
            [
                coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
                candle_frame("2025-01-03 08:00", close=99.0),
                candle_frame(
                    "2025-01-03 08:15",
                    close=99.4,
                    high=99.6,
                    low=99.2,
                ),
            ],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("short"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )

        self.assertEqual(result["stage"], "entry_confirmed")
        self.assertEqual(result["direction"], "short")
        self.assertAlmostEqual(float(result["initial_stop_price"]), 100.9)

    def test_daily_is_a_bias_not_a_stage_one_gate(self) -> None:
        result = scan_pair(
            candles4h=coil_frame("2025-01-01", periods=12, freq="4h"),
            candles15m=coil_frame("2025-01-02 20:00", periods=16, freq="15min"),
            candles1d=ordered_frame(
                "2024-12-30", periods=4, freq="1d", side="short"
            ),
            as_of=pd.Timestamp("2025-01-03 00:00", tz=UTC),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertEqual(result["daily_bias"], "short")

    def test_crypto_market_filter_is_applied_only_at_entry_confirmation(self) -> None:
        candles15m = confirmed_long_entry_frame()
        opposed = ordered_frame("2025-01-01", periods=13, freq="4h", side="short")

        blocked = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            btc4h=opposed,
            eth4h=opposed,
            inst_category="1",
            as_of=pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )
        non_crypto = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            btc4h=opposed,
            eth4h=opposed,
            inst_category="3",
            as_of=pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )

        self.assertEqual(blocked["stage"], "none")
        self.assertEqual(blocked["reason"], "market_filter_blocked")
        self.assertEqual(non_crypto["stage"], "entry_confirmed")

    def test_missing_or_duplicate_candles_are_rejected(self) -> None:
        missing = coil_frame("2025-01-01", periods=13, freq="4h").drop(index=6)
        duplicate = pd.concat(
            [
                coil_frame("2025-01-01", periods=12, freq="4h"),
                coil_frame("2025-01-01", periods=12, freq="4h").iloc[-1:],
            ],
            ignore_index=True,
        )
        candles15m = coil_frame("2025-01-02 20:00", periods=16, freq="15min")

        missing_result = scan_pair(
            candles4h=missing,
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 04:00", tz=UTC),
        )
        duplicate_result = scan_pair(
            candles4h=duplicate,
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 00:00", tz=UTC),
        )

        self.assertEqual(missing_result["stage"], "mature_15m_coil")
        self.assertEqual(duplicate_result["stage"], "mature_15m_coil")

    def test_future_rows_do_not_change_the_same_as_of_snapshot(self) -> None:
        candles4h = higher_breakout_frame("long")
        candles15m = coil_frame("2025-01-03 04:00", periods=16, freq="15min")
        as_of = pd.Timestamp("2025-01-03 08:00", tz=UTC)
        before = scan_pair(candles4h=candles4h, candles15m=candles15m, as_of=as_of)
        after = scan_pair(
            candles4h=pd.concat(
                [candles4h, candle_frame("2025-01-03 08:00", close=95.0)],
                ignore_index=True,
            ),
            candles15m=pd.concat(
                [candles15m, candle_frame("2025-01-03 08:00", close=95.0)],
                ignore_index=True,
            ),
            as_of=as_of,
        )

        self.assertEqual(before, after)

    def test_entry_confirmation_expires_after_the_next_open(self) -> None:
        candles15m = pd.concat(
            [
                confirmed_long_entry_frame(),
                candle_frame("2025-01-03 08:30", close=100.8),
            ],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:45", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["reason"], "entry_window_passed")

    def test_current_mature_coil_uses_current_observation_reason(self) -> None:
        result = scan_pair(
            candles4h=pd.DataFrame(),
            candles15m=layered_frame(raw_bars=135, mode="mature"),
            as_of=pd.Timestamp("2025-01-02 09:45", tz=UTC),
            params=ThreeStageParameters(),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")
        self.assertEqual(result["reason"], "fifteen_minute_coil_ready")

    def test_four_hour_breakout_expires_after_six_completed_bars(self) -> None:
        trailing = pd.concat(
            [
                candle_frame(
                    date.isoformat(),
                    close=102.0,
                    spread=3.0,
                )
                for date in pd.date_range(
                    "2025-01-03 04:00", periods=7, freq="4h", tz=UTC
                )
            ],
            ignore_index=True,
        )
        result = scan_pair(
            candles4h=pd.concat(
                [higher_breakout_frame("long"), trailing], ignore_index=True
            ),
            candles15m=coil_frame(
                "2025-01-04 04:00", periods=16, freq="15min"
            ),
            as_of=pd.Timestamp("2025-01-04 08:00", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["four_hour_context"], "mature_coil")

    def test_old_four_hour_coil_is_not_reported_as_current_after_it_disperses(self) -> None:
        dispersed = pd.concat(
            [
                candle_frame(date.isoformat(), close=100.0, spread=3.0)
                for date in pd.date_range(
                    "2025-01-03 00:00", periods=7, freq="4h", tz=UTC
                )
            ],
            ignore_index=True,
        )
        result = scan_pair(
            candles4h=pd.concat(
                [
                    coil_frame("2025-01-01", periods=12, freq="4h"),
                    dispersed,
                ],
                ignore_index=True,
            ),
            candles15m=coil_frame(
                "2025-01-04 00:00", periods=16, freq="15min"
            ),
            as_of=pd.Timestamp("2025-01-04 04:00", tz=UTC),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")

    def test_four_hour_episode_does_not_merge_after_leaving_compression(self) -> None:
        result = scan_pair(
            candles4h=pd.concat(
                [
                    coil_frame("2025-01-01", periods=12, freq="4h"),
                    candle_frame("2025-01-03 00:00", close=100.0, spread=3.0),
                    coil_frame("2025-01-03 04:00", periods=5, freq="4h", center=100.2),
                ],
                ignore_index=True,
            ),
            candles15m=coil_frame(
                "2025-01-03 20:00", periods=16, freq="15min"
            ),
            as_of=pd.Timestamp("2025-01-04 00:00", tz=UTC),
        )

        self.assertEqual(result["stage"], "mature_15m_coil")

    def test_missing_15m_after_four_hour_breakout_invalidates_setup(self) -> None:
        candles15m = coil_frame(
            "2025-01-03 04:00", periods=17, freq="15min"
        ).drop(index=8)

        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:15", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertIn("contiguous", str(result["reason"]))

    def test_stop_boundary_touch_invalidates_the_first_pullback(self) -> None:
        candles15m = pd.concat(
            [
                coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
                candle_frame("2025-01-03 08:00", close=101.0),
                candle_frame(
                    "2025-01-03 08:15",
                    close=100.6,
                    high=100.8,
                    low=99.1,
                ),
            ],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 08:30", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["reason"], "first_pullback_failed")

    def test_pre_breakout_15m_coil_is_not_reused_as_a_fresh_setup(self) -> None:
        candles15m = pd.concat(
            [
                coil_frame("2025-01-03 00:00", periods=16, freq="15min"),
                candle_frame("2025-01-03 04:00", close=100.0),
            ],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 04:15", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["reason"], "fifteen_minute_coil_not_ready")
        self.assertEqual(result["direction"], "long")
        self.assertEqual(
            result["four_hour_breakout_at"],
            pd.Timestamp("2025-01-03 04:00", tz=UTC),
        )

    def test_first_pullback_wait_expires_after_twelve_15m_bars(self) -> None:
        candles15m = pd.concat(
            [
                coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
                candle_frame("2025-01-03 08:00", close=101.0),
                *[
                    candle_frame(
                        (
                            pd.Timestamp("2025-01-03 08:15", tz=UTC)
                            + pd.Timedelta(minutes=15 * offset)
                        ).isoformat(),
                        close=102.0,
                        high=102.2,
                        low=101.8,
                    )
                    for offset in range(13)
                ],
            ],
            ignore_index=True,
        )

        result = scan_pair(
            candles4h=higher_breakout_frame("long"),
            candles15m=candles15m,
            as_of=pd.Timestamp("2025-01-03 11:30", tz=UTC),
        )

        self.assertEqual(result["stage"], "none")
        self.assertEqual(result["reason"], "first_pullback_expired")

    def test_collect_coil_zones_keeps_mature_band(self) -> None:
        zones = collect_coil_zones(
            coil_frame("2025-01-01", periods=16, freq="4h", center=108.0),
            ThreeStageParameters(),
            "4h",
        )

        self.assertGreaterEqual(len(zones), 1)
        self.assertAlmostEqual(float(zones[-1]["center"]), 108.0, delta=1.5)

    def test_pullback_20_odds_plan_uses_ma20_stop_and_previous_coil(self) -> None:
        fifteen = candle_frame("2025-01-03 08:15", close=100.0)
        fifteen.loc[:, ["ma20", "ema20", "atr14"]] = [99.6, 99.7, 1.0]
        result = {
            "stage": "wait_first_pullback",
            "direction": "long",
            "four_hour_context": "trend_aligned",
            "four_hour_context_direction": "long",
            "daily_bias": "long",
            "risk_labels": ["expanded_risk"],
            "initial_stop_price": 97.0,
        }

        plan = build_odds_plan(
            result,
            fifteen=fifteen,
            four_hour=coil_frame("2024-11-01", periods=16, freq="4h", center=108.0),
            daily=pd.DataFrame(),
            params=ThreeStageParameters(),
        )

        self.assertEqual(plan["playbook"], "pullback_20")
        self.assertTrue(plan["four_hour_expanded"])
        self.assertAlmostEqual(float(plan["odds_stop_price"]), 99.4, places=4)
        self.assertGreaterEqual(float(plan["planned_rr"]), 2.0)
        self.assertTrue(plan["odds_ok"])
        self.assertEqual(plan["target_source"], "4h_coil")

    def test_opposite_context_is_not_high_odds_pullback(self) -> None:
        fifteen = candle_frame("2025-01-03 08:15", close=100.0)
        result = {
            "stage": "entry_confirmed",
            "direction": "long",
            "four_hour_context": "opposite",
            "four_hour_context_direction": "short",
            "daily_bias": "short",
            "risk_labels": ["opposite"],
            "initial_stop_price": 99.0,
        }

        plan = build_odds_plan(
            result,
            fifteen=fifteen,
            four_hour=pd.DataFrame(),
            daily=pd.DataFrame(),
            params=ThreeStageParameters(),
        )

        self.assertNotEqual(plan["playbook"], "pullback_20")
        self.assertFalse(plan["odds_ok"])


def scan_pair(
    *,
    candles4h: pd.DataFrame,
    candles15m: pd.DataFrame,
    as_of: pd.Timestamp,
    candles1d: pd.DataFrame | None = None,
    btc4h: pd.DataFrame | None = None,
    eth4h: pd.DataFrame | None = None,
    inst_category: str = "3",
    params: ThreeStageParameters | None = None,
) -> dict[str, object]:
    # Existing lifecycle fixtures intentionally focus on state transitions and
    # provide short indicator-ready windows. New raw-history gate tests pass
    # explicit default parameters above.
    active_params = params or ThreeStageParameters(
        history_min_15m=0,
        history_min_15m_forming=0,
        history_min_15m_mature=0,
        history_min_4h=0,
        history_min_4h_forming=0,
        history_min_4h_mature=0,
        history_min_1d=0,
        history_min_1d_strong=0,
    )
    return scan_three_stage_pair(
        pair="ALT-USDT-SWAP",
        inst_category=inst_category,
        candles15m=candles15m,
        candles4h=candles4h,
        candles1d=candles1d if candles1d is not None else pd.DataFrame(),
        btc4h=btc4h if btc4h is not None else pd.DataFrame(),
        eth4h=eth4h if eth4h is not None else pd.DataFrame(),
        as_of=as_of,
        params=active_params,
    )


def coil_frame(
    start: str,
    *,
    periods: int,
    freq: str,
    center: float = 100.0,
    spread: float = 1.0,
    atr: float = 2.0,
) -> pd.DataFrame:
    normalized_freq = "1D" if freq == "1d" else freq
    dates = pd.date_range(start, periods=periods, freq=normalized_freq, tz=UTC)
    rows: list[dict[str, object]] = []
    for index, date in enumerate(dates):
        phase = 1 if index % 2 == 0 else -1
        averages = center + phase * np.linspace(-spread / 2, spread / 2, len(SIX))
        close = center + (0.25 if index % 2 == 0 else -0.25)
        rows.append(
            {
                "date": date,
                "open": close,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1.0,
                "atr14": atr,
                "ema60_slope3": 0.0,
                **{column: float(averages[position]) for position, column in enumerate(SIX)},
            }
        )
    return pd.DataFrame(rows)


def layered_frame(*, raw_bars: int, mode: str) -> pd.DataFrame:
    """Synthetic indicator-ready history for the layered state tests."""
    if mode not in {"forming", "mature"}:
        raise ValueError(mode)
    dates = pd.date_range(
        "2025-01-01", periods=raw_bars, freq="15min", tz=UTC
    )
    window = 8 if mode == "forming" else 16
    rows: list[dict[str, object]] = []
    for index, date in enumerate(dates):
        tail_index = index - (raw_bars - window)
        if tail_index < 0:
            spread = 6.0
        elif mode == "forming":
            spread = 4.0 if tail_index < 4 else 2.0
        else:
            spread = 1.0
        phase = 1 if index % 2 == 0 else -1
        averages = 100.0 + phase * np.linspace(-spread / 2, spread / 2, len(SIX))
        close = 100.25 if index % 2 == 0 else 99.75
        rows.append(
            {
                "date": date,
                "open": close,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1.0,
                "atr14": 2.0,
                "ema60_slope3": 0.0,
                **{
                    column: float(averages[position])
                    for position, column in enumerate(SIX)
                },
            }
        )
    return pd.DataFrame(rows)


def candle_frame(
    date: str,
    *,
    close: float,
    center: float = 100.0,
    spread: float = 1.0,
    atr: float = 2.0,
    high: float | None = None,
    low: float | None = None,
) -> pd.DataFrame:
    averages = np.linspace(center - spread / 2, center + spread / 2, len(SIX))
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(date, tz=UTC),
                "open": close,
                "high": close + 0.2 if high is None else high,
                "low": close - 0.2 if low is None else low,
                "close": close,
                "volume": 1.0,
                "atr14": atr,
                "ema60_slope3": 0.0,
                **{column: float(averages[position]) for position, column in enumerate(SIX)},
            }
        ]
    )


def higher_breakout_frame(side: str) -> pd.DataFrame:
    close = 101.0 if side == "long" else 99.0
    return pd.concat(
        [
            coil_frame("2025-01-01", periods=12, freq="4h"),
            candle_frame("2025-01-03 00:00", close=close),
        ],
        ignore_index=True,
    )


def confirmed_long_entry_frame() -> pd.DataFrame:
    return pd.concat(
        [
            coil_frame("2025-01-03 04:00", periods=16, freq="15min"),
            candle_frame("2025-01-03 08:00", close=101.0),
            candle_frame(
                "2025-01-03 08:15",
                close=100.6,
                high=100.8,
                low=100.4,
            ),
        ],
        ignore_index=True,
    )


def ordered_frame(
    start: str,
    *,
    periods: int,
    freq: str,
    side: str,
) -> pd.DataFrame:
    normalized_freq = "1D" if freq == "1d" else freq
    dates = pd.date_range(start, periods=periods, freq=normalized_freq, tz=UTC)
    rows: list[dict[str, object]] = []
    for index, date in enumerate(dates):
        center = 100.0 + (index * 0.1 if side == "long" else -index * 0.1)
        offsets = {
            "ma20": 0.5,
            "ma60": 0.0,
            "ma120": -0.5,
            "ema20": 0.4,
            "ema60": -0.1,
            "ema120": -0.6,
        }
        if side == "short":
            offsets = {column: -offset for column, offset in offsets.items()}
        close = center + (1.0 if side == "long" else -1.0)
        rows.append(
            {
                "date": date,
                "open": close,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1.0,
                "atr14": 2.0,
                "ema60_slope3": 0.3 if side == "long" else -0.3,
                **{column: center + offsets[column] for column in SIX},
            }
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
