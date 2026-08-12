"""Causal 4H/15m six-average screening state machine."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import SIX_AVERAGES, add_v2_indicators
from user_data.strategy_lib.okx_trend_compression_screener import trend_snapshot


STAGES = {
    "watch_both_coiled",
    "ready_4h_breakout_15m_coiled",
    "wait_first_pullback",
    "entry_confirmed",
    "none",
}


@dataclass(frozen=True)
class ThreeStageParameters:
    coil_window_4h: int = 12
    compression_4h_atr: float = 1.20
    min_compressed_fraction_4h: float = 8 / 12
    min_trailing_compressed_bars_4h: int = 4
    min_line_crossings_4h: int = 3
    min_line_crossing_bars_4h: int = 2
    min_center_crossings_4h: int = 2
    max_center_drift_4h_atr: float = 1.20
    coil_window_15m: int = 16
    compression_15m_atr: float = 2.0
    min_compressed_fraction_15m: float = 0.75
    min_trailing_compressed_bars_15m: int = 8
    min_line_crossings_15m: int = 4
    min_line_crossing_bars_15m: int = 3
    min_center_crossings_15m: int = 2
    max_center_drift_15m_atr: float = 1.0
    breakout_4h_atr: float = 0.10
    zone_breakout_wait_4h: int = 6
    setup_wait_15m: int = 48
    breakout_15m_atr: float = 0.10
    pullback_wait_15m: int = 12
    pullback_touch_atr: float = 0.20
    stop_buffer_atr: float = 0.20


@dataclass(frozen=True)
class _CoilRules:
    window: int
    compression_atr: float
    min_fraction: float
    min_trailing: int
    min_crossings: int
    min_crossing_bars: int
    min_center_crossings: int
    max_center_drift_atr: float
    interval: pd.Timedelta


def _rules(params: ThreeStageParameters, timeframe: str) -> _CoilRules:
    if timeframe == "4h":
        return _CoilRules(
            params.coil_window_4h,
            params.compression_4h_atr,
            params.min_compressed_fraction_4h,
            params.min_trailing_compressed_bars_4h,
            params.min_line_crossings_4h,
            params.min_line_crossing_bars_4h,
            params.min_center_crossings_4h,
            params.max_center_drift_4h_atr,
            pd.Timedelta(hours=4),
        )
    return _CoilRules(
        params.coil_window_15m,
        params.compression_15m_atr,
        params.min_compressed_fraction_15m,
        params.min_trailing_compressed_bars_15m,
        params.min_line_crossings_15m,
        params.min_line_crossing_bars_15m,
        params.min_center_crossings_15m,
        params.max_center_drift_15m_atr,
        pd.Timedelta(minutes=15),
    )


def _validate(params: ThreeStageParameters) -> None:
    for timeframe in ("4h", "15m"):
        rules = _rules(params, timeframe)
        if (
            rules.window < 2
            or rules.compression_atr <= 0
            or not 0 < rules.min_fraction <= 1
            or not 1 <= rules.min_trailing <= rules.window
            or rules.min_crossings < 1
            or not 1 <= rules.min_crossing_bars < rules.window
            or rules.min_center_crossings < 1
            or rules.max_center_drift_atr <= 0
        ):
            raise ValueError("invalid coil parameters")
    if min(
        params.breakout_4h_atr,
        params.zone_breakout_wait_4h,
        params.setup_wait_15m,
        params.breakout_15m_atr,
        params.pullback_wait_15m,
        params.pullback_touch_atr,
        params.stop_buffer_atr,
    ) < 0:
        raise ValueError("invalid state-machine parameters")


def _completed(
    frame: pd.DataFrame, as_of: pd.Timestamp, interval: pd.Timedelta
) -> tuple[pd.DataFrame, str | None]:
    if frame.empty or "date" not in frame:
        return pd.DataFrame(), "insufficient_data"
    source = frame.copy()
    source["date"] = pd.to_datetime(source["date"], utc=True).dt.as_unit("ns")
    if bool(source["date"].duplicated().any()):
        return pd.DataFrame(), "duplicate_candles"
    source = source.sort_values("date")
    source = source.loc[source["date"] + interval <= as_of].copy()
    if source.empty:
        return source, "insufficient_data"
    return add_v2_indicators(source).reset_index(drop=True), None


def _trend_direction(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    interval: pd.Timedelta,
) -> str:
    source, error = _completed(frame, as_of, interval)
    if error:
        return "neutral"
    direction = str(trend_snapshot(source).get("direction", "neutral"))
    return direction if direction in {"long", "short"} else "neutral"


def _market_allows_entry(
    direction: str,
    btc4h: pd.DataFrame,
    eth4h: pd.DataFrame,
    as_of: pd.Timestamp,
) -> bool:
    directions = {
        _trend_direction(btc4h, as_of, pd.Timedelta(hours=4)),
        _trend_direction(eth4h, as_of, pd.Timedelta(hours=4)),
    }
    opposite = "short" if direction == "long" else "long"
    return direction in directions and opposite not in directions


def _measure_coil(
    source: pd.DataFrame, end_index: int, rules: _CoilRules
) -> dict[str, object]:
    start = end_index - rules.window + 1
    if start < 0:
        return {"ready": False, "reason": "insufficient_data"}
    window = source.iloc[start : end_index + 1].copy()
    dates = pd.to_datetime(window["date"], utc=True)
    if not bool(dates.diff().iloc[1:].eq(rules.interval).all()):
        return {"ready": False, "reason": "non_contiguous_data"}
    columns = ["close", "atr14", *SIX_AVERAGES]
    numeric = window.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        return {"ready": False, "reason": "insufficient_data"}
    if bool((numeric["atr14"] <= 0).any()):
        return {"ready": False, "reason": "invalid_atr"}
    averages = numeric.loc[:, SIX_AVERAGES]
    highs = averages.max(axis=1)
    lows = averages.min(axis=1)
    centers = (highs + lows) / 2
    compression = (highs - lows) / numeric["atr14"]
    compressed = (compression <= rules.compression_atr).tolist()
    trailing = 0
    for flag in reversed(compressed):
        if not flag:
            break
        trailing += 1
    crossing_counts = np.zeros(len(window) - 1, dtype=int)
    for left, right in combinations(SIX_AVERAGES, 2):
        difference = (averages[left] - averages[right]).to_numpy(dtype=float)
        crossing_counts += ((difference[:-1] * difference[1:]) < 0).astype(int)
    price_difference = (numeric["close"] - centers).to_numpy(dtype=float)
    center_crossings = int(
        np.sum((price_difference[:-1] * price_difference[1:]) < 0)
    )
    median_atr = float(numeric["atr14"].median())
    center_drift = (float(centers.max()) - float(centers.min())) / median_atr
    fraction = float(np.mean(compressed))
    line_crossings = int(crossing_counts.sum())
    crossing_bars = int(np.sum(crossing_counts > 0))
    ready = bool(
        compressed[-1]
        and fraction >= rules.min_fraction
        and trailing >= rules.min_trailing
        and line_crossings >= rules.min_crossings
        and crossing_bars >= rules.min_crossing_bars
        and center_crossings >= rules.min_center_crossings
        and center_drift <= rules.max_center_drift_atr
    )
    trailing_slice = slice(len(window) - trailing, len(window))
    zone_high = float(highs.iloc[trailing_slice].max()) if trailing else np.nan
    zone_low = float(lows.iloc[trailing_slice].min()) if trailing else np.nan
    score = 100 * (
        0.30 * fraction
        + 0.20 * min(line_crossings / (2 * rules.min_crossings), 1.0)
        + 0.15 * min(crossing_bars / (2 * rules.min_crossing_bars), 1.0)
        + 0.20 * min(center_crossings / (2 * rules.min_center_crossings), 1.0)
        + 0.15 * max(0.0, 1 - center_drift / rules.max_center_drift_atr)
    )
    return {
        "ready": ready,
        "score": float(score),
        "zone_high": zone_high,
        "zone_low": zone_low,
        "atr": float(numeric["atr14"].iloc[-1]),
        "date": pd.Timestamp(window["date"].iloc[-1]),
        "reason": "ready" if ready else "coil_not_mature",
    }


def _empty(reason: str) -> dict[str, object]:
    return {
        "stage": "none",
        "direction": "none",
        "daily_bias": "neutral",
        "four_hour_state": "none",
        "four_hour_coil_score": 0.0,
        "four_hour_zone_high": np.nan,
        "four_hour_zone_low": np.nan,
        "four_hour_breakout_at": pd.NaT,
        "fifteen_minute_state": "none",
        "fifteen_minute_coil_score": 0.0,
        "fifteen_minute_zone_high": np.nan,
        "fifteen_minute_zone_low": np.nan,
        "fifteen_minute_breakout_at": pd.NaT,
        "first_pullback_at": pd.NaT,
        "initial_stop_price": np.nan,
        "next_executable_at": pd.NaT,
        "reason": reason,
    }


def _scan_four_hour(
    source: pd.DataFrame, params: ThreeStageParameters
) -> dict[str, object]:
    rules = _rules(params, "4h")
    zone: dict[str, object] | None = None
    breakout: dict[str, object] | None = None
    latest_coil: dict[str, object] | None = None
    latest_reason = "insufficient_data"
    episode_floor = 0
    previous_date: pd.Timestamp | None = None
    for index, row in source.iterrows():
        row_date = pd.Timestamp(row["date"])
        if (
            previous_date is not None
            and (zone is not None or breakout is not None)
            and row_date - previous_date != rules.interval
        ):
            return {
                "coil": None,
                "zone": None,
                "breakout": None,
                "reason": "four_hour_non_contiguous_data",
            }
        previous_date = row_date
        atr = float(row.get("atr14", np.nan))
        close = float(row.get("close", np.nan))
        if breakout is not None and index > int(breakout["index"]):
            breakout_age = index - int(breakout["index"])
            direction = str(breakout["direction"])
            invalid = (
                direction == "long" and close < float(breakout["low"])
            ) or (
                direction == "short" and close > float(breakout["high"])
            )
            if invalid:
                breakout = None
                zone = None
                latest_coil = None
                episode_floor = index
                latest_reason = "four_hour_breakout_invalidated"
            elif breakout_age > params.zone_breakout_wait_4h:
                breakout = None
                zone = None
                latest_coil = None
                episode_floor = index
                latest_reason = "four_hour_breakout_expired"
            else:
                continue

        if zone is not None and index > int(zone["last_coil_index"]):
            if close >= float(zone["high"]) + params.breakout_4h_atr * atr:
                breakout = {
                    "direction": "long",
                    "at": pd.Timestamp(row["date"]) + pd.Timedelta(hours=4),
                    "index": index,
                    "high": float(zone["high"]),
                    "low": float(zone["low"]),
                    "atr": atr,
                    "score": float(zone["score"]),
                }
                latest_reason = "four_hour_breakout_active"
                continue
            if close <= float(zone["low"]) - params.breakout_4h_atr * atr:
                breakout = {
                    "direction": "short",
                    "at": pd.Timestamp(row["date"]) + pd.Timedelta(hours=4),
                    "index": index,
                    "high": float(zone["high"]),
                    "low": float(zone["low"]),
                    "atr": atr,
                    "score": float(zone["score"]),
                }
                latest_reason = "four_hour_breakout_active"
                continue
            if index - int(zone["last_coil_index"]) > params.zone_breakout_wait_4h:
                zone = None
                episode_floor = index

        window_start = index - rules.window + 1
        measured = (
            _measure_coil(source, index, rules)
            if window_start >= episode_floor
            else {"ready": False, "reason": "new_four_hour_episode_not_mature"}
        )
        measured_reason = str(measured.get("reason", "coil_not_mature"))
        if latest_reason not in {
            "four_hour_breakout_expired",
            "four_hour_breakout_invalidated",
        }:
            latest_reason = measured_reason
        if bool(measured.get("ready")):
            if zone is not None and bool(zone.get("frozen")):
                latest_coil = None
                continue
            latest_reason = "ready"
            latest_coil = measured
            if zone is None:
                zone = {
                    "high": measured["zone_high"],
                    "low": measured["zone_low"],
                    "last_coil_index": index,
                    "score": measured["score"],
                    "frozen": False,
                }
            else:
                zone["high"] = max(float(zone["high"]), float(measured["zone_high"]))
                zone["low"] = min(float(zone["low"]), float(measured["zone_low"]))
                zone["last_coil_index"] = index
                zone["score"] = measured["score"]
        else:
            latest_coil = None
            if zone is not None and not bool(zone.get("frozen")):
                zone["frozen"] = True
                episode_floor = index
    return {
        "coil": latest_coil,
        "zone": zone,
        "breakout": breakout,
        "reason": latest_reason,
    }


def _scan_fifteen_minute(
    source: pd.DataFrame,
    higher_breakout: dict[str, object],
    params: ThreeStageParameters,
) -> dict[str, object]:
    rules = _rules(params, "15m")
    direction = str(higher_breakout["direction"])
    higher_available = pd.Timestamp(higher_breakout["at"])
    zone: dict[str, object] | None = None
    local_breakout: dict[str, object] | None = None
    rows_after_higher = 0
    last_coil: dict[str, object] | None = None

    relevant = source.loc[
        pd.to_datetime(source["date"], utc=True) >= higher_available
    ]
    relevant_dates = pd.to_datetime(relevant["date"], utc=True)
    if not relevant.empty and (
        pd.Timestamp(relevant_dates.iloc[0]) != higher_available
        or not bool(relevant_dates.diff().iloc[1:].eq(rules.interval).all())
    ):
        return {"state": "none", "reason": "fifteen_minute_non_contiguous_data"}

    for index, row in source.iterrows():
        available_at = pd.Timestamp(row["date"]) + pd.Timedelta(minutes=15)
        if available_at <= higher_available:
            continue
        rows_after_higher += 1
        if rows_after_higher > params.setup_wait_15m and local_breakout is None:
            return {"state": "none", "reason": "fifteen_minute_setup_expired"}
        atr = float(row.get("atr14", np.nan))
        close = float(row.get("close", np.nan))

        if local_breakout is not None:
            pullback_age = index - int(local_breakout["index"])
            if pullback_age > params.pullback_wait_15m:
                return {"state": "none", "reason": "first_pullback_expired"}
            if index == int(local_breakout["index"]):
                continue
            zone_high = float(local_breakout["high"])
            zone_low = float(local_breakout["low"])
            zone_atr = float(local_breakout["atr"])
            if direction == "long":
                invalid = float(row["low"]) <= (
                    zone_low - params.stop_buffer_atr * zone_atr
                )
                touched = float(row["low"]) <= (
                    zone_high + params.pullback_touch_atr * zone_atr
                )
                held = close >= zone_high
                stop = zone_low - params.stop_buffer_atr * zone_atr
            else:
                invalid = float(row["high"]) >= (
                    zone_high + params.stop_buffer_atr * zone_atr
                )
                touched = float(row["high"]) >= (
                    zone_low - params.pullback_touch_atr * zone_atr
                )
                held = close <= zone_low
                stop = zone_high + params.stop_buffer_atr * zone_atr
            if invalid or (touched and not held):
                return {"state": "none", "reason": "first_pullback_failed"}
            if touched:
                if index != len(source) - 1:
                    return {"state": "none", "reason": "entry_window_passed"}
                return {
                    "state": "entry_confirmed",
                    "reason": "first_pullback_held",
                    "score": local_breakout["score"],
                    "high": zone_high,
                    "low": zone_low,
                    "breakout_at": local_breakout["at"],
                    "first_pullback_at": available_at,
                    "next_executable_at": available_at,
                    "stop": stop,
                    "confirmed_index": index,
                }
            continue

        if zone is not None:
            if direction == "long" and close >= (
                float(zone["high"]) + params.breakout_15m_atr * atr
            ):
                local_breakout = {
                    **zone,
                    "at": available_at,
                    "index": index,
                    "atr": atr,
                }
                continue
            if direction == "short" and close <= (
                float(zone["low"]) - params.breakout_15m_atr * atr
            ):
                local_breakout = {
                    **zone,
                    "at": available_at,
                    "index": index,
                    "atr": atr,
                }
                continue
            if direction == "long" and close < float(zone["low"]):
                zone = None
            elif direction == "short" and close > float(zone["high"]):
                zone = None

        window_start = index - rules.window + 1
        fresh_window = bool(
            window_start >= 0
            and pd.Timestamp(source.iloc[window_start]["date"]) >= higher_available
        )
        measured = (
            _measure_coil(source, index, rules)
            if fresh_window
            else {"ready": False, "reason": "coil_predates_four_hour_breakout"}
        )
        if bool(measured.get("ready")):
            if zone is not None and bool(zone.get("frozen")):
                last_coil = None
                continue
            last_coil = measured
            if zone is None:
                zone = {
                    "high": measured["zone_high"],
                    "low": measured["zone_low"],
                    "score": measured["score"],
                    "frozen": False,
                }
            else:
                zone["high"] = max(float(zone["high"]), float(measured["zone_high"]))
                zone["low"] = min(float(zone["low"]), float(measured["zone_low"]))
                zone["score"] = measured["score"]
        else:
            last_coil = None
            if zone is not None and not bool(zone.get("frozen")):
                zone["frozen"] = True

    if local_breakout is not None:
        return {
            "state": "wait_first_pullback",
            "reason": "fifteen_minute_breakout_waiting_for_pullback",
            "score": local_breakout["score"],
            "high": local_breakout["high"],
            "low": local_breakout["low"],
            "breakout_at": local_breakout["at"],
        }
    if zone is not None and last_coil is not None:
        return {
            "state": "coiled",
            "reason": "fifteen_minute_coil_ready",
            "score": zone["score"],
            "high": zone["high"],
            "low": zone["low"],
        }
    return {"state": "none", "reason": "fifteen_minute_coil_not_ready"}


def scan_three_stage_pair(
    *,
    pair: str,
    inst_category: str,
    candles15m: pd.DataFrame,
    candles4h: pd.DataFrame,
    candles1d: pd.DataFrame,
    btc4h: pd.DataFrame,
    eth4h: pd.DataFrame,
    as_of: pd.Timestamp,
    params: ThreeStageParameters,
) -> dict[str, object]:
    """Return the latest causal screening state for one contract."""
    del pair
    _validate(params)
    cutoff = pd.Timestamp(as_of)
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    daily_bias = _trend_direction(candles1d, cutoff, pd.Timedelta(days=1))
    four_hour, error4h = _completed(candles4h, cutoff, pd.Timedelta(hours=4))
    fifteen, error15m = _completed(candles15m, cutoff, pd.Timedelta(minutes=15))
    if error4h or error15m:
        result = _empty(error4h or error15m or "insufficient_data")
        result["daily_bias"] = daily_bias
        return result
    higher = _scan_four_hour(four_hour, params)
    coil4h = higher["coil"] or {"ready": False}
    coil15m = _measure_coil(fifteen, len(fifteen) - 1, _rules(params, "15m"))
    breakout = higher["breakout"]
    if breakout is not None:
        lower = _scan_fifteen_minute(fifteen, breakout, params)
        lower_state = str(lower["state"])
        if lower_state != "none":
            if (
                lower_state == "entry_confirmed"
                and inst_category == "1"
                and not _market_allows_entry(
                    str(breakout["direction"]), btc4h, eth4h, cutoff
                )
            ):
                result = _empty("market_filter_blocked")
                result["daily_bias"] = daily_bias
                return result
            stage = {
                "coiled": "ready_4h_breakout_15m_coiled",
                "wait_first_pullback": "wait_first_pullback",
                "entry_confirmed": "entry_confirmed",
            }[lower_state]
            result = _empty(str(lower["reason"]))
            direction = str(breakout["direction"])
            result.update(
                {
                    "stage": stage,
                    "direction": direction,
                    "daily_bias": daily_bias,
                    "four_hour_state": f"breakout_{direction}",
                    "four_hour_coil_score": breakout["score"],
                    "four_hour_zone_high": breakout["high"],
                    "four_hour_zone_low": breakout["low"],
                    "four_hour_breakout_at": breakout["at"],
                    "fifteen_minute_state": (
                        f"breakout_{direction}"
                        if lower_state in {"wait_first_pullback", "entry_confirmed"}
                        else "coiled"
                    ),
                    "fifteen_minute_coil_score": lower.get("score", 0.0),
                    "fifteen_minute_zone_high": lower.get("high", np.nan),
                    "fifteen_minute_zone_low": lower.get("low", np.nan),
                    "fifteen_minute_breakout_at": lower.get("breakout_at", pd.NaT),
                    "first_pullback_at": lower.get("first_pullback_at", pd.NaT),
                    "initial_stop_price": lower.get("stop", np.nan),
                    "next_executable_at": lower.get("next_executable_at", pd.NaT),
                }
            )
            return result
        result = _empty(str(lower["reason"]))
        direction = str(breakout["direction"])
        result.update(
            {
                "direction": direction,
                "daily_bias": daily_bias,
                "four_hour_state": f"breakout_{direction}",
                "four_hour_coil_score": breakout["score"],
                "four_hour_zone_high": breakout["high"],
                "four_hour_zone_low": breakout["low"],
                "four_hour_breakout_at": breakout["at"],
            }
        )
        return result
    if bool(coil4h.get("ready")) and bool(coil15m.get("ready")):
        result = _empty("both_timeframes_coiled")
        result.update(
            {
                "stage": "watch_both_coiled",
                "daily_bias": daily_bias,
                "four_hour_state": "coiled",
                "four_hour_coil_score": coil4h["score"],
                "four_hour_zone_high": coil4h["zone_high"],
                "four_hour_zone_low": coil4h["zone_low"],
                "fifteen_minute_state": "coiled",
                "fifteen_minute_coil_score": coil15m["score"],
                "fifteen_minute_zone_high": coil15m["zone_high"],
                "fifteen_minute_zone_low": coil15m["zone_low"],
            }
        )
        return result
    reason = str(higher.get("reason", "no_active_three_stage_setup"))
    if reason == "non_contiguous_data":
        reason = "four_hour_non_contiguous_data"
    elif reason in {"ready", "coil_not_mature", "insufficient_data"}:
        reason = "no_active_three_stage_setup"
    result = _empty(reason)
    result["daily_bias"] = daily_bias
    return result
