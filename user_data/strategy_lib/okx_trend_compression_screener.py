"""Causal daily/4H trend plus 15m six-average coil screener."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import SIX_AVERAGES, add_v2_indicators


@dataclass(frozen=True)
class ScreenerParameters:
    compression_15m_atr: float = 2.0
    min_age_days: int = 30
    coil_window_15m: int = 16
    min_compressed_fraction: float = 0.75
    min_line_crossings: int = 4
    min_center_crossings: int = 2
    max_center_drift_atr: float = 1.0


def _validate_parameters(params: ScreenerParameters) -> None:
    if (
        params.compression_15m_atr <= 0
        or params.min_age_days < 0
        or params.coil_window_15m < 2
        or not 0 < params.min_compressed_fraction <= 1
        or params.min_line_crossings < 1
        or params.min_center_crossings < 1
        or params.max_center_drift_atr <= 0
    ):
        raise ValueError("screener thresholds must be positive and bounded")


def trend_snapshot(frame: pd.DataFrame) -> dict[str, object]:
    """Return the latest completed-candle EMA20/60 trend classification."""
    if frame.empty:
        return {"direction": "insufficient", "strict_six": False}
    source = add_v2_indicators(frame)
    row = source.iloc[-1]
    required = ("close", "ema20", "ema60", "ema60_slope3")
    if not all(np.isfinite(float(row.get(column, np.nan))) for column in required):
        return {"direction": "insufficient", "strict_six": False}
    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema60 = float(row["ema60"])
    slope = float(row["ema60_slope3"])
    if close > ema20 > ema60 and slope > 0:
        direction = "long"
    elif close < ema20 < ema60 and slope < 0:
        direction = "short"
    else:
        direction = "neutral"

    strict_six = False
    if all(np.isfinite(float(row.get(column, np.nan))) for column in SIX_AVERAGES):
        if direction == "long":
            strict_six = bool(
                close > max(float(row[column]) for column in SIX_AVERAGES)
                and float(row["ma20"]) > float(row["ma60"]) > float(row["ma120"])
                and float(row["ema20"]) > float(row["ema60"]) > float(row["ema120"])
            )
        elif direction == "short":
            strict_six = bool(
                close < min(float(row[column]) for column in SIX_AVERAGES)
                and float(row["ma20"]) < float(row["ma60"]) < float(row["ma120"])
                and float(row["ema20"]) < float(row["ema60"]) < float(row["ema120"])
            )
    atr = float(row.get("atr14", np.nan))
    strength = abs(ema20 - ema60) / atr if np.isfinite(atr) and atr > 0 else np.nan
    return {
        "direction": direction,
        "strict_six": strict_six,
        "strength_atr": strength,
        "close": close,
        "date": pd.Timestamp(row["date"]),
    }


def compression_snapshot(frame: pd.DataFrame) -> dict[str, object]:
    """Return the latest six-average spread normalized by ATR and price."""
    if frame.empty:
        return {"compression_atr": np.nan, "compression_price": np.nan}
    source = add_v2_indicators(frame)
    row = source.iloc[-1]
    averages = [float(row.get(column, np.nan)) for column in SIX_AVERAGES]
    atr = float(row.get("atr14", np.nan))
    close = float(row.get("close", np.nan))
    if not all(np.isfinite(value) for value in (*averages, atr, close)) or atr <= 0 or close <= 0:
        return {"compression_atr": np.nan, "compression_price": np.nan}
    spread = max(averages) - min(averages)
    return {
        "compression_atr": spread / atr,
        "compression_price": spread / close,
        "cluster_high": max(averages),
        "cluster_low": min(averages),
        "close": close,
        "date": pd.Timestamp(row["date"]),
    }


def _sign_crossings(values: pd.Series) -> int:
    """Count direct sign flips without inventing crossings across missing values."""
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    if len(numeric) < 2:
        return 0
    previous = numeric[:-1]
    current = numeric[1:]
    valid = np.isfinite(previous) & np.isfinite(current)
    return int(np.sum(valid & ((previous * current) < 0)))


def coil_snapshot(
    frame: pd.DataFrame, params: ScreenerParameters | None = None
) -> dict[str, object]:
    """Measure whether the trailing 15m window is a sustained sideways coil.

    A coil is deliberately stricter than a single compressed candle: most bars
    must remain compressed, the six averages must exchange order, price must
    oscillate around their centre, and that centre may not drift materially.
    """
    active = params or ScreenerParameters()
    _validate_parameters(active)
    empty = {
        "current_compression_atr": np.nan,
        "compression_price": np.nan,
        "compressed_fraction": np.nan,
        "line_crossings": 0,
        "center_crossings": 0,
        "center_drift_atr": np.nan,
        "coil_score": 0.0,
        "coil_ready": False,
        "coil_reason": "insufficient_15m_data",
    }
    if frame.empty:
        return empty
    source = add_v2_indicators(frame)
    if len(source) < active.coil_window_15m:
        return empty
    window = source.iloc[-active.coil_window_15m :].copy()
    required = ["date", "close", "atr14", *SIX_AVERAGES]
    if any(column not in window for column in required):
        return empty
    dates = pd.to_datetime(window["date"], utc=True)
    if not bool(dates.diff().iloc[1:].eq(pd.Timedelta(minutes=15)).all()):
        return {**empty, "coil_reason": "non_contiguous_15m_data"}
    numeric_columns = ["close", "atr14", *SIX_AVERAGES]
    numeric = window.loc[:, numeric_columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        return empty
    if bool((numeric["atr14"] <= 0).any()) or bool((numeric["close"] <= 0).any()):
        return empty

    averages = numeric.loc[:, SIX_AVERAGES]
    cluster_high = averages.max(axis=1)
    cluster_low = averages.min(axis=1)
    cluster_center = (cluster_high + cluster_low) / 2
    compression_atr = (cluster_high - cluster_low) / numeric["atr14"]
    compressed_fraction = float(
        (compression_atr <= active.compression_15m_atr).mean()
    )
    line_crossings = sum(
        _sign_crossings(averages[left] - averages[right])
        for left, right in combinations(SIX_AVERAGES, 2)
    )
    center_crossings = _sign_crossings(numeric["close"] - cluster_center)
    median_atr = float(numeric["atr14"].median())
    center_drift_atr = (
        float(cluster_center.max()) - float(cluster_center.min())
    ) / median_atr
    current_compression = float(compression_atr.iloc[-1])

    latest_compressed = current_compression <= active.compression_15m_atr
    sustained = compressed_fraction >= active.min_compressed_fraction
    intertwined = line_crossings >= active.min_line_crossings
    oscillating = center_crossings >= active.min_center_crossings
    sideways = center_drift_atr <= active.max_center_drift_atr
    coil_ready = bool(
        latest_compressed and sustained and intertwined and oscillating and sideways
    )
    if not latest_compressed:
        reason = "latest_not_compressed"
    elif not sustained:
        reason = "compression_not_sustained"
    elif not intertwined:
        reason = "averages_not_intertwined"
    elif not oscillating:
        reason = "price_not_oscillating"
    elif not sideways:
        reason = "cluster_center_drifting"
    else:
        reason = "ready_waiting_for_expansion"

    compression_score = compressed_fraction
    crossing_score = min(line_crossings / (2 * active.min_line_crossings), 1.0)
    oscillation_score = min(
        center_crossings / (2 * active.min_center_crossings), 1.0
    )
    drift_score = max(
        0.0, 1.0 - center_drift_atr / active.max_center_drift_atr
    )
    coil_score = 100 * (
        0.35 * compression_score
        + 0.30 * crossing_score
        + 0.20 * oscillation_score
        + 0.15 * drift_score
    )
    close = float(numeric["close"].iloc[-1])
    return {
        "current_compression_atr": current_compression,
        "compression_price": float(cluster_high.iloc[-1] - cluster_low.iloc[-1])
        / close,
        "compressed_fraction": compressed_fraction,
        "line_crossings": int(line_crossings),
        "center_crossings": int(center_crossings),
        "center_drift_atr": float(center_drift_atr),
        "coil_score": float(coil_score),
        "coil_ready": coil_ready,
        "coil_reason": reason,
        "cluster_high": float(cluster_high.iloc[-1]),
        "cluster_low": float(cluster_low.iloc[-1]),
        "close": close,
        "date": pd.Timestamp(window["date"].iloc[-1]),
    }


def screen_instruments(
    instruments: list[dict[str, object]],
    daily_frames: dict[str, pd.DataFrame],
    four_hour_frames: dict[str, pd.DataFrame],
    fifteen_minute_frames: dict[str, pd.DataFrame],
    params: ScreenerParameters | None = None,
) -> pd.DataFrame:
    """Build a ranked snapshot; candidates have aligned trends and a mature coil."""
    active = params or ScreenerParameters()
    _validate_parameters(active)
    rows: list[dict[str, object]] = []
    for instrument in instruments:
        instrument_id = str(instrument["instId"])
        age_days = int(instrument.get("ageDays") or 0)
        daily = trend_snapshot(daily_frames.get(instrument_id, pd.DataFrame()))
        four_hour = trend_snapshot(four_hour_frames.get(instrument_id, pd.DataFrame()))
        coil = coil_snapshot(
            fifteen_minute_frames.get(instrument_id, pd.DataFrame()), active
        )
        direction = (
            str(daily["direction"])
            if daily["direction"] == four_hour["direction"]
            and daily["direction"] in {"long", "short"}
            else "none"
        )
        compression_atr = float(coil.get("current_compression_atr", np.nan))
        old_enough = age_days >= active.min_age_days
        eligibility_reason = str(instrument.get("eligibility_reason") or "eligible")
        instrument_eligible = eligibility_reason == "eligible"
        candidate = bool(
            instrument_eligible
            and old_enough
            and direction in {"long", "short"}
            and bool(coil.get("coil_ready", False))
        )
        if eligibility_reason == "unknown_category":
            status = "unknown_category"
        elif not old_enough:
            status = "listed_too_recently"
        elif daily["direction"] == "insufficient":
            status = "insufficient_daily_data"
        elif daily["direction"] == "neutral":
            status = "daily_not_trending"
        elif four_hour["direction"] == "insufficient":
            status = "insufficient_4h_data"
        elif direction == "none":
            status = "daily_4h_not_aligned"
        elif not np.isfinite(compression_atr):
            status = "insufficient_15m_data"
        elif candidate:
            status = "candidate_coiled_waiting_for_expansion"
        else:
            status = "aligned_coil_forming"
        rows.append(
            {
                "instrument": instrument_id,
                "base": str(instrument.get("base") or instrument_id.split("-")[0]),
                "direction": direction,
                "status": status,
                "candidate": candidate,
                "age_days": age_days,
                "daily_trend": daily["direction"],
                "4h_trend": four_hour["direction"],
                "daily_strict_six": bool(daily.get("strict_six", False)),
                "4h_strict_six": bool(four_hour.get("strict_six", False)),
                "daily_strength_atr": daily.get("strength_atr", np.nan),
                "4h_strength_atr": four_hour.get("strength_atr", np.nan),
                "compression_15m_atr": compression_atr,
                "current_compression_atr": compression_atr,
                "compression_15m_pct": float(
                    coil.get("compression_price", np.nan)
                ),
                "compression_price": float(
                    coil.get("compression_price", np.nan)
                ),
                "coil_window_bars": active.coil_window_15m,
                "compressed_fraction": float(
                    coil.get("compressed_fraction", np.nan)
                ),
                "line_crossings": int(coil.get("line_crossings", 0)),
                "center_crossings": int(coil.get("center_crossings", 0)),
                "center_drift_atr": float(
                    coil.get("center_drift_atr", np.nan)
                ),
                "coil_score": float(coil.get("coil_score", 0.0)),
                "coil_ready": bool(coil.get("coil_ready", False)),
                "coil_reason": str(
                    coil.get("coil_reason", "insufficient_15m_data")
                ),
                "last_price": coil.get("close", np.nan),
                "daily_candle": daily.get("date", pd.NaT),
                "4h_candle": four_hour.get("date", pd.NaT),
                "15m_candle": coil.get("date", pd.NaT),
            }
        )
    output = pd.DataFrame(rows)
    if output.empty:
        return output
    output["direction_order"] = output["direction"].map(
        {"long": 0, "short": 1, "none": 2}
    )
    output = output.sort_values(
        ["candidate", "coil_score", "direction_order", "compression_15m_atr", "base"],
        ascending=[False, False, True, True, True],
        na_position="last",
    ).drop(columns="direction_order")
    return output.reset_index(drop=True)
