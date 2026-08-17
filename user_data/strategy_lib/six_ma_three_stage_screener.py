"""Causal 4H/15m six-average screening state machine."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import SIX_AVERAGES, add_v2_indicators
from user_data.strategy_lib.okx_trend_compression_screener import trend_snapshot


STAGES = {
    "wait_first_pullback",
    "entry_confirmed",
    "mature_15m_coil",
    "forming_15m_coil",
    "none",
}


@dataclass(frozen=True)
class ThreeStageParameters:
    coil_window_4h: int = 12
    compression_4h_atr: float = 1.60
    min_compressed_fraction_4h: float = 7 / 12
    min_trailing_compressed_bars_4h: int = 3
    min_line_crossings_4h: int = 2
    min_line_crossing_bars_4h: int = 2
    min_center_crossings_4h: int = 1
    max_center_drift_4h_atr: float = 1.80
    coil_window_4h_forming: int = 8
    compression_4h_forming_atr: float = 2.40
    min_compressed_fraction_4h_forming: float = 0.50
    min_trailing_compressed_bars_4h_forming: int = 2
    min_line_crossings_4h_forming: int = 1
    min_line_crossing_bars_4h_forming: int = 1
    min_center_crossings_4h_forming: int = 0
    min_contraction_ratio_4h_forming: float = 0.20
    max_center_drift_4h_forming_atr: float = 2.40
    coil_window_15m: int = 16
    compression_15m_atr: float = 2.0
    min_compressed_fraction_15m: float = 10 / 16
    min_trailing_compressed_bars_15m: int = 4
    min_line_crossings_15m: int = 3
    min_line_crossing_bars_15m: int = 2
    min_center_crossings_15m: int = 2
    max_center_drift_15m_atr: float = 1.40
    coil_window_15m_forming: int = 8
    compression_15m_forming_atr: float = 2.40
    min_compressed_fraction_15m_forming: float = 0.50
    min_trailing_compressed_bars_15m_forming: int = 2
    min_line_crossings_15m_forming: int = 1
    min_line_crossing_bars_15m_forming: int = 1
    min_center_crossings_15m_forming: int = 0
    min_contraction_ratio_15m_forming: float = 0.20
    max_center_drift_15m_forming_atr: float = 1.80
    breakout_4h_atr: float = 0.10
    zone_breakout_wait_4h: int = 6
    setup_wait_15m: int = 48
    breakout_15m_atr: float = 0.10
    pullback_wait_15m: int = 12
    pullback_touch_atr: float = 0.20
    stop_buffer_atr: float = 0.20
    history_min_15m: int = 120
    history_min_15m_forming: int = 127
    history_min_15m_mature: int = 135
    history_min_4h: int = 120
    history_min_4h_forming: int = 127
    history_min_4h_mature: int = 131
    history_min_1d: int = 63
    history_min_1d_strong: int = 120
    max_observation_items: int = 20
    min_planned_rr: float = 2.0


@dataclass(frozen=True)
class _CoilRules:
    mode: str
    window: int
    compression_atr: float
    min_fraction: float
    min_trailing: int
    min_crossings: int
    min_crossing_bars: int
    min_center_crossings: int
    max_center_drift_atr: float
    min_contraction_ratio: float
    interval: pd.Timedelta


def _rules(
    params: ThreeStageParameters, timeframe: str, mode: str = "mature"
) -> _CoilRules:
    if mode not in {"mature", "forming"}:
        raise ValueError(f"unknown coil mode: {mode}")
    if timeframe == "4h" and mode == "forming":
        return _CoilRules(
            mode,
            params.coil_window_4h_forming,
            params.compression_4h_forming_atr,
            params.min_compressed_fraction_4h_forming,
            params.min_trailing_compressed_bars_4h_forming,
            params.min_line_crossings_4h_forming,
            params.min_line_crossing_bars_4h_forming,
            params.min_center_crossings_4h_forming,
            params.max_center_drift_4h_forming_atr,
            params.min_contraction_ratio_4h_forming,
            pd.Timedelta(hours=4),
        )
    if timeframe == "4h":
        return _CoilRules(
            mode,
            params.coil_window_4h,
            params.compression_4h_atr,
            params.min_compressed_fraction_4h,
            params.min_trailing_compressed_bars_4h,
            params.min_line_crossings_4h,
            params.min_line_crossing_bars_4h,
            params.min_center_crossings_4h,
            params.max_center_drift_4h_atr,
            0.0,
            pd.Timedelta(hours=4),
        )
    if mode == "forming":
        return _CoilRules(
            mode,
            params.coil_window_15m_forming,
            params.compression_15m_forming_atr,
            params.min_compressed_fraction_15m_forming,
            params.min_trailing_compressed_bars_15m_forming,
            params.min_line_crossings_15m_forming,
            params.min_line_crossing_bars_15m_forming,
            params.min_center_crossings_15m_forming,
            params.max_center_drift_15m_forming_atr,
            params.min_contraction_ratio_15m_forming,
            pd.Timedelta(minutes=15),
        )
    return _CoilRules(
        mode,
        params.coil_window_15m,
        params.compression_15m_atr,
        params.min_compressed_fraction_15m,
        params.min_trailing_compressed_bars_15m,
        params.min_line_crossings_15m,
        params.min_line_crossing_bars_15m,
        params.min_center_crossings_15m,
        params.max_center_drift_15m_atr,
        0.0,
        pd.Timedelta(minutes=15),
    )


def _validate(params: ThreeStageParameters) -> None:
    for timeframe in ("4h", "15m"):
        for mode in ("mature", "forming"):
            rules = _rules(params, timeframe, mode)
            if (
                rules.window < 2
                or rules.compression_atr <= 0
                or not 0 < rules.min_fraction <= 1
                or not 1 <= rules.min_trailing <= rules.window
                or rules.min_crossings < 1
                or not 1 <= rules.min_crossing_bars < rules.window
                or rules.min_center_crossings < 0
                or rules.max_center_drift_atr <= 0
                or rules.min_contraction_ratio < 0
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
        params.history_min_15m,
        params.history_min_15m_forming,
        params.history_min_15m_mature,
        params.history_min_4h,
        params.history_min_4h_forming,
        params.history_min_4h_mature,
        params.history_min_1d,
        params.history_min_1d_strong,
        params.max_observation_items,
    ) < 0:
        raise ValueError("invalid state-machine parameters")
    if params.min_planned_rr <= 0:
        raise ValueError("invalid state-machine parameters")


def _completed(
    frame: pd.DataFrame, as_of: pd.Timestamp, interval: pd.Timedelta
) -> tuple[pd.DataFrame, str | None]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "date" not in frame:
        return pd.DataFrame(), "insufficient_data"
    source = frame.copy()
    try:
        source["date"] = pd.to_datetime(
            source["date"], utc=True, errors="raise"
        ).dt.as_unit("ns")
    except (TypeError, ValueError, OverflowError):
        return pd.DataFrame(), "malformed_data"
    if bool(source["date"].duplicated().any()):
        return pd.DataFrame(), "duplicate_candles"
    source = source.sort_values("date")
    source = source.loc[source["date"] + interval <= as_of].copy()
    if source.empty:
        return source, "insufficient_data"
    try:
        return add_v2_indicators(source).reset_index(drop=True), None
    except (KeyError, TypeError, ValueError):
        return pd.DataFrame(), "malformed_data"


def _raw_completed_count(
    frame: pd.DataFrame, as_of: pd.Timestamp, interval: pd.Timedelta
) -> tuple[int, str | None]:
    """Count completed source bars before indicator derivation.

    This deliberately counts raw rows so prefilled MA/EMA columns cannot
    bypass the minimum history required to form a valid causal window.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty or "date" not in frame:
        return 0, "insufficient_data"
    try:
        dates = pd.to_datetime(
            frame["date"], utc=True, errors="raise"
        ).dt.as_unit("ns")
    except (TypeError, ValueError, OverflowError):
        return 0, "malformed_data"
    if bool(dates.duplicated().any()):
        return 0, "duplicate_candles"
    return int((dates + interval <= as_of).sum()), None


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


def _daily_direction(frame: pd.DataFrame) -> str:
    """Classify a completed daily EMA20/EMA60 trend without raising."""
    if frame.empty:
        return "unknown"
    source = frame.copy()
    try:
        source["date"] = pd.to_datetime(source["date"], utc=True)
        source = source.sort_values("date")
        source = add_v2_indicators(source)
    except (KeyError, TypeError, ValueError):
        return "unknown"
    if len(source) < 3:
        return "unknown"
    row = source.iloc[-1]
    try:
        ema20 = float(row.get("ema20", np.nan))
        ema60 = float(row.get("ema60", np.nan))
        slope = float(row.get("ema60_slope3", np.nan))
        close = float(row.get("close", np.nan))
    except (TypeError, ValueError):
        return "unknown"
    if not all(np.isfinite(value) for value in (ema20, ema60, slope, close)):
        return "unknown"
    if close > ema20 > ema60 and slope > 0:
        return "long"
    if close < ema20 < ema60 and slope < 0:
        return "short"
    return "neutral"


def _strong_direction(frame: pd.DataFrame) -> str:
    """Return a strict six-line direction for the latest completed 4H row."""
    if frame.empty:
        return "unknown"
    try:
        source = add_v2_indicators(frame)
        row = source.iloc[-1]
        values = [float(row.get(column, np.nan)) for column in SIX_AVERAGES]
        close = float(row.get("close", np.nan))
        slope = float(row.get("ema60_slope3", np.nan))
    except (IndexError, KeyError, TypeError, ValueError):
        return "unknown"
    if not all(np.isfinite(value) for value in (*values, close, slope)):
        return "unknown"
    ma20, ma60, ma120, ema20, ema60, ema120 = values
    if (
        close > max(values)
        and ma20 > ma60 > ma120
        and ema20 > ema60 > ema120
        and slope > 0
    ):
        return "long"
    if (
        close < min(values)
        and ma20 < ma60 < ma120
        and ema20 < ema60 < ema120
        and slope < 0
    ):
        return "short"
    return "neutral"


def classify_four_hour_context(
    four_hour: pd.DataFrame,
    daily: pd.DataFrame,
    fifteen_direction: str = "neutral",
    params: ThreeStageParameters | None = None,
    *,
    four_hour_raw_bars: int | None = None,
    daily_raw_bars: int | None = None,
) -> dict[str, object]:
    """Classify loose 4H/daily context for a 15m observation.

    Missing or malformed higher-timeframe data yields ``unknown`` and risk
    labels; it never raises or invalidates the lower-timeframe observation.
    """
    active = params or ThreeStageParameters()
    try:
        _validate(active)
    except ValueError:
        # The classifier is intentionally total for callers receiving external
        # parameter payloads; the main scanner still validates before use.
        active = ThreeStageParameters()
    effective_4h_bars = len(four_hour) if four_hour_raw_bars is None else four_hour_raw_bars
    effective_daily_bars = len(daily) if daily_raw_bars is None else daily_raw_bars
    daily_direction = (
        _daily_direction(daily)
        if effective_daily_bars >= active.history_min_1d
        else "unknown"
    )
    daily_strong_direction = (
        _strong_direction(daily)
        if effective_daily_bars >= active.history_min_1d_strong
        else "unknown"
    )
    strong_4h = (
        _strong_direction(four_hour)
        if effective_4h_bars >= active.history_min_4h
        else "unknown"
    )
    risk_labels: list[str] = []
    if daily_direction == "unknown":
        risk_labels.append("daily_unknown")
    elif daily_strong_direction == "unknown":
        risk_labels.append("daily_strong_unknown")
    if strong_4h == "unknown":
        risk_labels.append("four_hour_unknown")

    try:
        mature = (
            _measure_coil(
                four_hour, len(four_hour) - 1, _rules(active, "4h", "mature")
            )
            if not four_hour.empty
            else _measurement_empty("insufficient_data", mode="mature")
        )
        forming = (
            _measure_coil(
                four_hour, len(four_hour) - 1, _rules(active, "4h", "forming")
            )
            if not four_hour.empty
            else _measurement_empty("insufficient_data", mode="forming")
        )
    except (KeyError, TypeError, ValueError, IndexError):
        mature = _measurement_empty("malformed_data", mode="mature")
        forming = _measurement_empty("malformed_data", mode="forming")
    mature_ok = bool(mature.get("ready")) and (
        effective_4h_bars >= active.history_min_4h_mature
    )
    forming_ok = bool(forming.get("ready")) and (
        effective_4h_bars >= active.history_min_4h_forming
    )

    current_compression = float(mature.get("current_compression_atr", np.nan))
    if not np.isfinite(current_compression):
        current_compression = float(forming.get("current_compression_atr", np.nan))
    if np.isfinite(current_compression) and current_compression > active.compression_4h_atr:
        risk_labels.append("expanded_risk")

    lower_direction = str(fifteen_direction)
    if lower_direction not in {"long", "short"}:
        lower_direction = "neutral"
    opposing = "short" if lower_direction == "long" else "long"
    if lower_direction != "neutral" and (
        strong_4h == opposing or daily_direction == opposing
    ):
        risk_labels.append("opposite")
        return {
            "state": "opposite",
            "score": 0.0,
            "direction": opposing,
            "daily_direction": daily_direction,
            "daily_strong_direction": daily_strong_direction,
            "risk_labels": sorted(set(risk_labels)),
        }
    if mature_ok:
        state = "mature_coil"
        score = float(mature.get("score", 0.0)) * 0.40
    elif forming_ok:
        state = "forming_coil"
        score = float(forming.get("score", 0.0)) * 0.25
    elif daily_direction in {"long", "short"} and strong_4h == daily_direction:
        state = "trend_aligned"
        score = 35.0
    elif daily_direction in {"long", "short"}:
        state = "daily_trend_only"
        score = 20.0
        risk_labels.append("higher_timeframe_unconfirmed")
    elif strong_4h in {"long", "short"}:
        state = "unknown"
        score = 0.0
        risk_labels.append("daily_not_aligned")
    else:
        state = "unknown"
        score = 0.0
    return {
        "state": state,
        "score": float(min(max(score, 0.0), 100.0)),
        "direction": strong_4h if strong_4h in {"long", "short"} else daily_direction,
        "daily_direction": daily_direction,
        "daily_strong_direction": daily_strong_direction,
        "risk_labels": sorted(set(risk_labels)),
    }


# Private alias kept for tests and callers that use the original module style.
_classify_four_hour_context = classify_four_hour_context


def odds_field_defaults() -> dict[str, object]:
    """Stable odds-plan fields for empty or ineligible scanner rows."""
    return {
        "playbook": "none",
        "four_hour_expanded": False,
        "odds_stop_price": np.nan,
        "target_price": np.nan,
        "planned_rr": np.nan,
        "odds_ok": False,
        "target_source": "none",
    }


def collect_coil_zones(
    source: pd.DataFrame,
    params: ThreeStageParameters,
    timeframe: str,
) -> list[dict[str, float | int]]:
    """Merge consecutive mature six-line coils into distinct historical zones."""
    if source.empty or timeframe not in {"4h", "1d"}:
        return []
    rules = _rules(params, "4h", "mature")
    if timeframe == "1d":
        rules = _CoilRules(
            rules.mode,
            rules.window,
            rules.compression_atr,
            rules.min_fraction,
            rules.min_trailing,
            rules.min_crossings,
            rules.min_crossing_bars,
            rules.min_center_crossings,
            rules.max_center_drift_atr,
            rules.min_contraction_ratio,
            pd.Timedelta(days=1),
        )
    zones: list[dict[str, float | int]] = []
    current: dict[str, float | int] | None = None
    start_index = max(rules.window - 1, 0)
    for index in range(start_index, len(source)):
        measured = _measure_coil(source, index, rules)
        if not bool(measured.get("ready")):
            if current is not None:
                zones.append(current)
                current = None
            continue
        high = float(measured["zone_high"])
        low = float(measured["zone_low"])
        if not np.isfinite(high) or not np.isfinite(low) or high <= low:
            continue
        if current is None:
            current = {
                "high": high,
                "low": low,
                "center": (high + low) / 2,
                "first_index": index,
                "last_index": index,
            }
            continue
        current["high"] = max(float(current["high"]), high)
        current["low"] = min(float(current["low"]), low)
        current["center"] = (float(current["high"]) + float(current["low"])) / 2
        current["last_index"] = index
    if current is not None:
        zones.append(current)
    return zones


def _is_four_hour_expanded(result: dict[str, object]) -> bool:
    context = str(result.get("four_hour_context") or "").strip().lower()
    risks = {str(item).strip().lower() for item in (result.get("risk_labels") or [])}
    direction = str(result.get("direction") or "").strip().lower()
    htf = str(result.get("four_hour_context_direction") or "").strip().lower()
    daily = str(result.get("daily_bias") or result.get("daily_direction") or "").strip().lower()
    if context == "opposite" or "opposite" in risks:
        return False
    if context in {"mature_coil", "forming_coil"}:
        return False
    if direction not in {"long", "short"}:
        return False
    aligned = htf == direction or daily == direction
    if not aligned:
        return False
    return context == "trend_aligned" or "expanded_risk" in risks


def _classify_playbook(result: dict[str, object], four_hour_expanded: bool) -> str:
    stage = str(result.get("stage") or "").strip().lower()
    direction = str(result.get("direction") or "").strip().lower()
    if direction not in {"long", "short"}:
        return "none"
    if stage in {"entry_confirmed", "wait_first_pullback"} and four_hour_expanded:
        return "pullback_20"
    if stage in {"entry_confirmed", "wait_first_pullback", "mature_15m_coil"}:
        return "coil_retest"
    return "observation"


def _pullback_20_stop(
    row: pd.Series,
    direction: str,
    params: ThreeStageParameters,
) -> float:
    try:
        ma20 = float(row.get("ma20", np.nan))
        ema20 = float(row.get("ema20", np.nan))
        atr = float(row.get("atr14", np.nan))
    except (TypeError, ValueError):
        return float("nan")
    if not all(np.isfinite(value) for value in (ma20, ema20, atr)) or atr <= 0:
        return float("nan")
    buffer = params.stop_buffer_atr * atr
    if direction == "long":
        return min(ma20, ema20) - buffer
    return max(ma20, ema20) + buffer


def _coil_stop(result: dict[str, object], direction: str, params: ThreeStageParameters) -> float:
    existing = result.get("initial_stop_price")
    try:
        stop = float(existing)
    except (TypeError, ValueError):
        stop = float("nan")
    if np.isfinite(stop):
        return stop
    try:
        high = float(result.get("fifteen_minute_zone_high", np.nan))
        low = float(result.get("fifteen_minute_zone_low", np.nan))
        atr = float(result.get("fifteen_minute_current_compression_atr", np.nan))
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(high) or not np.isfinite(low):
        return float("nan")
    buffer = params.stop_buffer_atr * atr if np.isfinite(atr) and atr > 0 else 0.0
    if direction == "long":
        return low - buffer
    return high + buffer


def _previous_coil_target(
    zones: list[dict[str, float | int]],
    *,
    entry: float,
    direction: str,
    atr: float,
) -> tuple[float, dict[str, float | int]] | None:
    if not np.isfinite(entry) or direction not in {"long", "short"}:
        return None
    clearance = atr * 0.20 if np.isfinite(atr) and atr > 0 else 0.0
    candidates: list[tuple[float, float, dict[str, float | int]]] = []
    for zone in zones:
        low = float(zone["low"])
        high = float(zone["high"])
        center = float(zone["center"])
        if not np.isfinite(low) or not np.isfinite(high) or not np.isfinite(center):
            continue
        if low - clearance <= entry <= high + clearance:
            continue
        if direction == "long" and low > entry:
            candidates.append((low - entry, center, zone))
        elif direction == "short" and high < entry:
            candidates.append((entry - high, center, zone))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _distance, target, zone = candidates[0]
    return target, zone


def build_odds_plan(
    result: dict[str, object],
    *,
    fifteen: pd.DataFrame,
    four_hour: pd.DataFrame,
    daily: pd.DataFrame,
    params: ThreeStageParameters,
) -> dict[str, object]:
    """Attach the highest-RR playbook fields without changing stage logic."""
    plan = odds_field_defaults()
    direction = str(result.get("direction") or "").strip().lower()
    four_hour_expanded = _is_four_hour_expanded(result)
    playbook = _classify_playbook(result, four_hour_expanded)
    plan["four_hour_expanded"] = four_hour_expanded
    plan["playbook"] = playbook
    if fifteen.empty or direction not in {"long", "short"}:
        return plan
    row = fifteen.iloc[-1]
    try:
        entry = float(row.get("close", np.nan))
        atr = float(row.get("atr14", np.nan))
    except (TypeError, ValueError):
        return plan
    if not np.isfinite(entry):
        return plan
    if playbook == "pullback_20":
        stop = _pullback_20_stop(row, direction, params)
    else:
        stop = _coil_stop(result, direction, params)
        if not np.isfinite(stop) and playbook == "coil_retest":
            stop = _pullback_20_stop(row, direction, params)
    plan["odds_stop_price"] = stop
    if not np.isfinite(stop):
        return plan
    risk = (entry - stop) if direction == "long" else (stop - entry)
    if risk <= 0:
        return plan
    target = None
    source = "none"
    four_hour_hit = _previous_coil_target(
        collect_coil_zones(four_hour, params, "4h"),
        entry=entry,
        direction=direction,
        atr=atr,
    )
    if four_hour_hit is not None:
        target, _zone = four_hour_hit
        source = "4h_coil"
    else:
        daily_hit = _previous_coil_target(
            collect_coil_zones(daily, params, "1d"),
            entry=entry,
            direction=direction,
            atr=atr,
        )
        if daily_hit is not None:
            target, _zone = daily_hit
            source = "daily_coil"
    if target is None or not np.isfinite(target):
        return plan
    reward = (target - entry) if direction == "long" else (entry - target)
    if reward <= 0:
        return plan
    planned_rr = float(reward / risk)
    plan["target_price"] = float(target)
    plan["planned_rr"] = planned_rr
    plan["target_source"] = source
    plan["odds_ok"] = bool(
        playbook == "pullback_20" and planned_rr >= params.min_planned_rr
    )
    return plan


def _attach_odds_plan(
    result: dict[str, object],
    *,
    fifteen: pd.DataFrame,
    four_hour: pd.DataFrame,
    daily: pd.DataFrame,
    params: ThreeStageParameters,
) -> dict[str, object]:
    result.update(
        build_odds_plan(
            result,
            fifteen=fifteen,
            four_hour=four_hour,
            daily=daily,
            params=params,
        )
    )
    return result


def _measurement_empty(reason: str, *, mode: str = "mature") -> dict[str, object]:
    """Return the stable measurement contract for an invalid window."""
    return {
        "state": "none",
        "ready": False,
        "score": 0.0,
        "current_compression_atr": np.nan,
        "compressed_fraction": 0.0,
        "trailing_compressed_bars": 0,
        "contraction_ratio": 0.0,
        "zone_high": np.nan,
        "zone_low": np.nan,
        "atr": np.nan,
        "date": pd.NaT,
        "reason": reason,
        "mode": mode,
    }


def _measure_coil(
    source: pd.DataFrame,
    end_index: int,
    rules: _CoilRules | ThreeStageParameters,
    mode: str | None = None,
) -> dict[str, object]:
    """Measure one causal six-line window in mature or forming mode.

    ``rules`` remains accepted as the third positional argument for backwards
    compatibility.  Passing ``ThreeStageParameters`` is a convenience for
    callers that need the new public ``mode`` selector.
    """
    if isinstance(rules, ThreeStageParameters):
        active_mode = mode or "mature"
        rules = _rules(rules, "15m", active_mode)
    else:
        active_mode = mode or rules.mode
    start = end_index - rules.window + 1
    if start < 0:
        return _measurement_empty("insufficient_data", mode=active_mode)
    window = source.iloc[start : end_index + 1].copy()
    dates = pd.to_datetime(window["date"], utc=True)
    if not bool(dates.diff().iloc[1:].eq(rules.interval).all()):
        return _measurement_empty("non_contiguous_data", mode=active_mode)
    columns = ["close", "atr14", *SIX_AVERAGES]
    if any(column not in window for column in columns):
        return _measurement_empty("insufficient_data", mode=active_mode)
    numeric = window.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        return _measurement_empty("insufficient_data", mode=active_mode)
    if bool((numeric["atr14"] <= 0).any()):
        return _measurement_empty("invalid_atr", mode=active_mode)
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
    half = max(1, len(window) // 2)
    previous_span = float((highs.iloc[:half]).mean()) - float(
        (lows.iloc[:half]).mean()
    )
    recent_span = float((highs.iloc[-half:]).mean()) - float(
        (lows.iloc[-half:]).mean()
    )
    contraction_ratio = (
        float(1.0 - recent_span / previous_span)
        if previous_span > 0
        else 0.0
    )
    mature_ready = bool(
        compressed[-1]
        and fraction >= rules.min_fraction
        and trailing >= rules.min_trailing
        and line_crossings >= rules.min_crossings
        and crossing_bars >= rules.min_crossing_bars
        and center_crossings >= rules.min_center_crossings
        and center_drift <= rules.max_center_drift_atr
    )
    ready = bool(
        mature_ready
        and (
            rules.mode == "mature"
            or contraction_ratio >= rules.min_contraction_ratio
        )
    )
    trailing_slice = slice(len(window) - trailing, len(window))
    zone_high = float(highs.iloc[trailing_slice].max()) if trailing else np.nan
    zone_low = float(lows.iloc[trailing_slice].min()) if trailing else np.nan
    score = 100 * (
        0.30 * fraction
        + 0.20 * min(line_crossings / (2 * rules.min_crossings), 1.0)
        + 0.15 * min(crossing_bars / (2 * rules.min_crossing_bars), 1.0)
        + 0.20
        * (
            1.0
            if rules.min_center_crossings == 0
            else min(center_crossings / (2 * rules.min_center_crossings), 1.0)
        )
        + 0.15 * max(0.0, 1 - center_drift / rules.max_center_drift_atr)
    )
    return {
        "state": (
            f"{rules.mode}_coil" if ready else "none"
        ),
        "ready": ready,
        "score": float(score) if ready else 0.0,
        "current_compression_atr": float(compression.iloc[-1]),
        "compressed_fraction": fraction,
        "trailing_compressed_bars": trailing,
        "contraction_ratio": contraction_ratio,
        "zone_high": zone_high,
        "zone_low": zone_low,
        "atr": float(numeric["atr14"].iloc[-1]),
        "date": pd.Timestamp(window["date"].iloc[-1]),
        "reason": (
            f"{rules.mode}_coil" if ready else f"{rules.mode}_coil_not_ready"
        ),
        "mode": rules.mode,
        "line_crossings": line_crossings,
        "line_crossing_bars": crossing_bars,
        "center_crossings": center_crossings,
        "center_drift_atr": center_drift,
    }


def _empty(reason: str) -> dict[str, object]:
    return {
        "stage": "none",
        "direction": "none",
        "daily_bias": "neutral",
        "daily_direction": "neutral",
        "daily_strong_direction": "unknown",
        "four_hour_state": "none",
        "four_hour_context": "unknown",
        "four_hour_context_score": 0.0,
        "four_hour_context_direction": "neutral",
        "risk_labels": [],
        "four_hour_coil_score": 0.0,
        "four_hour_zone_high": np.nan,
        "four_hour_zone_low": np.nan,
        "four_hour_breakout_at": pd.NaT,
        "fifteen_minute_state": "none",
        "fifteen_minute_coil_score": 0.0,
        "fifteen_minute_current_compression_atr": np.nan,
        "fifteen_minute_compressed_fraction": 0.0,
        "fifteen_minute_trailing_compressed_bars": 0,
        "fifteen_minute_contraction_ratio": 0.0,
        "fifteen_minute_zone_high": np.nan,
        "fifteen_minute_zone_low": np.nan,
        "fifteen_minute_breakout_at": pd.NaT,
        "first_pullback_at": pd.NaT,
        "initial_stop_price": np.nan,
        "next_executable_at": pd.NaT,
        "reason": reason,
        **odds_field_defaults(),
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
    higher_breakout: dict[str, object] | None,
    params: ThreeStageParameters,
) -> dict[str, object]:
    if source.empty:
        return {"state": "none", "reason": "insufficient_data"}
    rules = _rules(params, "15m")
    direction = (
        str(higher_breakout["direction"]) if higher_breakout is not None else None
    )
    higher_available = (
        pd.Timestamp(higher_breakout["at"])
        if higher_breakout is not None
        else pd.Timestamp(source["date"].iloc[0])
    )
    zone: dict[str, object] | None = None
    local_breakout: dict[str, object] | None = None
    rows_after_higher = 0
    last_coil: dict[str, object] | None = None

    relevant = source.loc[pd.to_datetime(source["date"], utc=True) >= higher_available]
    relevant_dates = pd.to_datetime(relevant["date"], utc=True)
    if higher_breakout is not None and not relevant.empty and (
        pd.Timestamp(relevant_dates.iloc[0]) != higher_available
        or not bool(relevant_dates.diff().iloc[1:].eq(rules.interval).all())
    ):
        return {"state": "none", "reason": "fifteen_minute_non_contiguous_data"}

    for index, row in source.iterrows():
        available_at = pd.Timestamp(row["date"]) + pd.Timedelta(minutes=15)
        if available_at <= higher_available:
            continue
        rows_after_higher += 1
        if (
            higher_breakout is not None
            and rows_after_higher > params.setup_wait_15m
            and local_breakout is None
        ):
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
            local_direction = str(local_breakout["direction"])
            ma20 = float(row.get("ma20", np.nan))
            ema20 = float(row.get("ema20", np.nan))
            if np.isfinite(ma20) and np.isfinite(ema20):
                fast_low = min(ma20, ema20)
                fast_high = max(ma20, ema20)
                touch_low = fast_low - params.pullback_touch_atr * atr
                touch_high = fast_high + params.pullback_touch_atr * atr
                touched = bool(
                    float(row["high"]) >= touch_low
                    and float(row["low"]) <= touch_high
                )
            else:
                touched = False
            if local_direction == "long":
                invalid = float(row["low"]) <= (
                    zone_low - params.stop_buffer_atr * zone_atr
                )
                held = close >= zone_high
                stop = zone_low - params.stop_buffer_atr * zone_atr
            else:
                invalid = float(row["high"]) >= (
                    zone_high + params.stop_buffer_atr * zone_atr
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
                    "direction": local_direction,
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
            long_breakout = close >= float(zone["high"]) + params.breakout_15m_atr * atr
            short_breakout = close <= float(zone["low"]) - params.breakout_15m_atr * atr
            if (direction in {None, "long"}) and long_breakout:
                local_breakout = {
                    **zone,
                    "direction": "long",
                    "at": available_at,
                    "index": index,
                    "atr": atr,
                }
                continue
            if (direction in {None, "short"}) and short_breakout:
                local_breakout = {
                    **zone,
                    "direction": "short",
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
            and (
                higher_breakout is None
                or pd.Timestamp(source.iloc[window_start]["date"]) >= higher_available
            )
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
            "direction": local_breakout["direction"],
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


def _six_line_bias(source: pd.DataFrame) -> str:
    """Return a descriptive price-vs-six-line bias for observation rows."""
    if source.empty:
        return "neutral"
    row = source.iloc[-1]
    try:
        values = np.asarray([float(row.get(column, np.nan)) for column in SIX_AVERAGES])
        close = float(row.get("close", np.nan))
    except (TypeError, ValueError):
        return "neutral"
    if not np.isfinite(values).all() or not np.isfinite(close):
        return "neutral"
    center = float((values.max() + values.min()) / 2)
    below = int(np.sum(values < close))
    above = int(np.sum(values > close))
    if close > center and below >= 4:
        return "long"
    if close < center and above >= 4:
        return "short"
    return "neutral"


def _apply_context(result: dict[str, object], context: dict[str, object]) -> None:
    state = str(context.get("state", "unknown"))
    direction = str(context.get("direction", "neutral"))
    daily = str(context.get("daily_direction", "unknown"))
    result["four_hour_context"] = state
    result["four_hour_context_score"] = float(context.get("score", 0.0))
    result["four_hour_context_direction"] = direction
    result["daily_direction"] = daily
    result["daily_strong_direction"] = str(
        context.get("daily_strong_direction", "unknown")
    )
    result["daily_bias"] = daily if daily != "unknown" else "neutral"
    result["risk_labels"] = list(context.get("risk_labels", []))


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
    raw15_count, raw15_error = _raw_completed_count(
        candles15m, cutoff, pd.Timedelta(minutes=15)
    )
    raw4_count, raw4_error = _raw_completed_count(
        candles4h, cutoff, pd.Timedelta(hours=4)
    )
    raw1d_count, raw1d_error = _raw_completed_count(
        candles1d, cutoff, pd.Timedelta(days=1)
    )
    four_hour, error4h = _completed(candles4h, cutoff, pd.Timedelta(hours=4))
    fifteen, error15m = _completed(candles15m, cutoff, pd.Timedelta(minutes=15))
    daily, error1d = _completed(candles1d, cutoff, pd.Timedelta(days=1))
    result: dict[str, object] = _empty("insufficient_data")
    try:
        if raw15_error or error15m:
            insufficient_reason = raw15_error or error15m or "insufficient_15m_window"
            if insufficient_reason == "insufficient_data":
                insufficient_reason = "insufficient_15m_data"
            result = _empty(insufficient_reason)
            context = classify_four_hour_context(
                four_hour,
                daily if not error1d else pd.DataFrame(),
                "neutral",
                params,
                four_hour_raw_bars=raw4_count,
                daily_raw_bars=raw1d_count,
            )
            _apply_context(result, context)
            return result
        if raw15_count < params.history_min_15m:
            result = _empty("insufficient_15m_data")
            context = classify_four_hour_context(
                four_hour if not error4h else pd.DataFrame(),
                daily if not error1d else pd.DataFrame(),
                "neutral",
                params,
                four_hour_raw_bars=raw4_count,
                daily_raw_bars=raw1d_count,
            )
            _apply_context(result, context)
            return result
        # Higher-timeframe data is deliberately optional.  It contributes context
        # and risk labels, but never deletes a valid 15m observation.
        if error4h:
            four_hour = pd.DataFrame()
        higher = (
            _scan_four_hour(four_hour, params)
            if raw4_count >= params.history_min_4h_mature
            else {
                "coil": None,
                "zone": None,
                "breakout": None,
                "reason": "four_hour_history_insufficient",
            }
        )
        coil4h = higher["coil"] or {"ready": False}
        coil15m = _measure_coil(
            fifteen, len(fifteen) - 1, _rules(params, "15m", "mature")
        )
        forming15m = _measure_coil(
            fifteen, len(fifteen) - 1, _rules(params, "15m", "forming")
        )
        observation_direction = _six_line_bias(fifteen)
        context = classify_four_hour_context(
            four_hour,
            daily if not error1d else pd.DataFrame(),
            observation_direction,
            params,
            four_hour_raw_bars=raw4_count,
            daily_raw_bars=raw1d_count,
        )
        breakout = higher["breakout"]
        # If the 4H itself broke out, use that side; otherwise scan the 15m core
        # from its own mature episode (the 15m-first path).
        lower = (
            _scan_fifteen_minute(fifteen, breakout, params)
            if raw15_count >= params.history_min_15m_mature
            else {"state": "none", "reason": "fifteen_minute_history_insufficient"}
        )
        lower_state = str(lower.get("state", "none"))
        if lower_state in {"wait_first_pullback", "entry_confirmed"}:
            direction = str(lower.get("direction") or (breakout or {}).get("direction", "neutral"))
            context = classify_four_hour_context(
                four_hour,
                daily if not error1d else pd.DataFrame(),
                direction,
                params,
                four_hour_raw_bars=raw4_count,
                daily_raw_bars=raw1d_count,
            )
            if lower_state == "entry_confirmed" and "opposite" in context.get("risk_labels", []):
                result = _empty("opposite_four_hour_context")
                _apply_context(result, context)
                result.update(
                    {
                        "direction": direction,
                        "fifteen_minute_state": "expired_or_invalid",
                        "fifteen_minute_coil_score": 0.0,
                    }
                )
                return result
            if (
                lower_state == "entry_confirmed"
                and inst_category == "1"
                and not _market_allows_entry(direction, btc4h, eth4h, cutoff)
            ):
                result = _empty("market_filter_blocked")
                _apply_context(result, context)
                return result
            result = _empty(str(lower.get("reason", "active_setup")))
            _apply_context(result, context)
            result.update(
                {
                    "stage": "entry_confirmed" if lower_state == "entry_confirmed" else "wait_first_pullback",
                    "direction": direction,
                    "four_hour_state": (
                        f"breakout_{direction}" if breakout is not None else context["state"]
                    ),
                    "four_hour_coil_score": (breakout or {}).get("score", coil4h.get("score", 0.0)),
                    "four_hour_zone_high": (breakout or {}).get("high", coil4h.get("zone_high", np.nan)),
                    "four_hour_zone_low": (breakout or {}).get("low", coil4h.get("zone_low", np.nan)),
                    "four_hour_breakout_at": (breakout or {}).get("at", pd.NaT),
                    "fifteen_minute_state": f"breakout_{direction}",
                    "fifteen_minute_coil_score": float(lower.get("score", 0.0)),
                    "fifteen_minute_zone_high": lower.get("high", np.nan),
                    "fifteen_minute_zone_low": lower.get("low", np.nan),
                    "fifteen_minute_breakout_at": lower.get("breakout_at", pd.NaT),
                    "first_pullback_at": lower.get("first_pullback_at", pd.NaT),
                    "initial_stop_price": lower.get("stop", np.nan),
                    "next_executable_at": lower.get("next_executable_at", pd.NaT),
                }
            )
            return result

        result = _empty(str(lower.get("reason", "no_active_three_stage_setup")))
        _apply_context(result, context)
        if breakout is None and str(lower.get("reason", "")) in {
            "first_pullback_failed",
            "first_pullback_expired",
            "fifteen_minute_setup_expired",
        }:
            result["fifteen_minute_state"] = "expired_or_invalid"
            result["fifteen_minute_coil_score"] = 0.0
            return result
        # Once a higher-timeframe breakout has an active lifecycle, an expired,
        # invalid, or gapped lower setup must stay dead; do not revive the prior
        # mature snapshot as a fresh observation.
        if breakout is not None and lower_state == "none":
            result.update(
                {
                    "direction": str(breakout.get("direction", "none")),
                    "four_hour_state": f"breakout_{breakout['direction']}",
                    "four_hour_coil_score": breakout.get("score", 0.0),
                    "four_hour_zone_high": breakout.get("high", np.nan),
                    "four_hour_zone_low": breakout.get("low", np.nan),
                    "four_hour_breakout_at": breakout.get("at", pd.NaT),
                    "fifteen_minute_state": "expired_or_invalid",
                    "fifteen_minute_coil_score": 0.0,
                }
            )
            return result
        if str(higher.get("reason", "")) in {
            "four_hour_breakout_expired",
            "four_hour_breakout_invalidated",
        }:
            result["fifteen_minute_state"] = "expired_or_invalid"
            result["fifteen_minute_coil_score"] = 0.0
            return result
        mature_15m_ready = bool(coil15m.get("ready")) and (
            raw15_count >= params.history_min_15m_mature
        )
        forming_15m_ready = bool(forming15m.get("ready")) and (
            raw15_count >= params.history_min_15m_forming
        )
        if (lower_state == "coiled" and raw15_count >= params.history_min_15m_mature) or mature_15m_ready:
            stage_direction = (
                str(breakout.get("direction"))
                if breakout is not None
                else observation_direction
            )
            result.update(
                {
                    "stage": "mature_15m_coil",
                    "reason": "fifteen_minute_coil_ready",
                    "direction": stage_direction,
                    "four_hour_state": (
                        f"breakout_{breakout['direction']}" if breakout is not None else context["state"]
                    ),
                    "four_hour_coil_score": (breakout or {}).get("score", coil4h.get("score", 0.0)),
                    "four_hour_zone_high": (breakout or {}).get("high", coil4h.get("zone_high", np.nan)),
                    "four_hour_zone_low": (breakout or {}).get("low", coil4h.get("zone_low", np.nan)),
                    "four_hour_breakout_at": (breakout or {}).get("at", pd.NaT),
                    "fifteen_minute_state": "mature_coil",
                    "fifteen_minute_coil_score": float(coil15m.get("score", lower.get("score", 0.0))),
                    "fifteen_minute_current_compression_atr": float(coil15m.get("current_compression_atr", np.nan)),
                    "fifteen_minute_compressed_fraction": float(coil15m.get("compressed_fraction", 0.0)),
                    "fifteen_minute_trailing_compressed_bars": int(coil15m.get("trailing_compressed_bars", 0)),
                    "fifteen_minute_contraction_ratio": float(coil15m.get("contraction_ratio", 0.0)),
                    "fifteen_minute_zone_high": lower.get("high", coil15m.get("zone_high", np.nan)),
                    "fifteen_minute_zone_low": lower.get("low", coil15m.get("zone_low", np.nan)),
                }
            )
            return result
        if forming_15m_ready:
            result.update(
                {
                    "stage": "forming_15m_coil",
                    "reason": "fifteen_minute_coil_forming",
                    "direction": observation_direction,
                    "four_hour_state": context["state"],
                    "four_hour_coil_score": coil4h.get("score", 0.0),
                    "four_hour_zone_high": coil4h.get("zone_high", np.nan),
                    "four_hour_zone_low": coil4h.get("zone_low", np.nan),
                    "fifteen_minute_state": "forming_coil",
                    "fifteen_minute_coil_score": float(forming15m.get("score", 0.0)),
                    "fifteen_minute_current_compression_atr": float(forming15m.get("current_compression_atr", np.nan)),
                    "fifteen_minute_compressed_fraction": float(forming15m.get("compressed_fraction", 0.0)),
                    "fifteen_minute_trailing_compressed_bars": int(forming15m.get("trailing_compressed_bars", 0)),
                    "fifteen_minute_contraction_ratio": float(forming15m.get("contraction_ratio", 0.0)),
                    "fifteen_minute_zone_high": forming15m.get("zone_high", np.nan),
                    "fifteen_minute_zone_low": forming15m.get("zone_low", np.nan),
                }
            )
            return result
        if str(lower.get("reason")) in {
            "fifteen_minute_setup_expired",
            "first_pullback_expired",
            "first_pullback_failed",
        }:
            result["fifteen_minute_state"] = "expired_or_invalid"
            result["fifteen_minute_coil_score"] = 0.0
        else:
            lower_reason = str(lower.get("reason", ""))
            result["reason"] = (
                lower_reason
                if lower_reason and lower_reason != "fifteen_minute_coil_not_ready"
                else "no_active_three_stage_setup"
            )
        return result
    finally:
        _attach_odds_plan(
            result,
            fifteen=fifteen,
            four_hour=four_hour if not error4h else pd.DataFrame(),
            daily=daily if not error1d else pd.DataFrame(),
            params=params,
        )
