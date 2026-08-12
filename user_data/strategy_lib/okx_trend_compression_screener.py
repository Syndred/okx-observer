"""Causal daily/4H trend plus 15m six-average compression screener."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import SIX_AVERAGES, add_v2_indicators


@dataclass(frozen=True)
class ScreenerParameters:
    compression_15m_atr: float = 2.0
    min_age_days: int = 30


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


def screen_instruments(
    instruments: list[dict[str, object]],
    daily_frames: dict[str, pd.DataFrame],
    four_hour_frames: dict[str, pd.DataFrame],
    fifteen_minute_frames: dict[str, pd.DataFrame],
    params: ScreenerParameters | None = None,
) -> pd.DataFrame:
    """Build a ranked snapshot; exact candidates have aligned trends and compression."""
    active = params or ScreenerParameters()
    if active.compression_15m_atr <= 0 or active.min_age_days < 0:
        raise ValueError("screener thresholds must be positive")
    rows: list[dict[str, object]] = []
    for instrument in instruments:
        instrument_id = str(instrument["instId"])
        age_days = int(instrument.get("ageDays") or 0)
        daily = trend_snapshot(daily_frames.get(instrument_id, pd.DataFrame()))
        four_hour = trend_snapshot(four_hour_frames.get(instrument_id, pd.DataFrame()))
        compression = compression_snapshot(
            fifteen_minute_frames.get(instrument_id, pd.DataFrame())
        )
        direction = (
            str(daily["direction"])
            if daily["direction"] == four_hour["direction"]
            and daily["direction"] in {"long", "short"}
            else "none"
        )
        compression_atr = float(compression.get("compression_atr", np.nan))
        old_enough = age_days >= active.min_age_days
        eligibility_reason = str(instrument.get("eligibility_reason") or "eligible")
        instrument_eligible = eligibility_reason == "eligible"
        candidate = bool(
            instrument_eligible
            and old_enough
            and direction in {"long", "short"}
            and np.isfinite(compression_atr)
            and compression_atr <= active.compression_15m_atr
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
            status = "candidate"
        else:
            status = "aligned_waiting_for_compression"
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
                "compression_15m_pct": float(
                    compression.get("compression_price", np.nan)
                ),
                "last_price": compression.get("close", np.nan),
                "daily_candle": daily.get("date", pd.NaT),
                "4h_candle": four_hour.get("date", pd.NaT),
                "15m_candle": compression.get("date", pd.NaT),
            }
        )
    output = pd.DataFrame(rows)
    if output.empty:
        return output
    output["direction_order"] = output["direction"].map(
        {"long": 0, "short": 1, "none": 2}
    )
    output = output.sort_values(
        ["candidate", "direction_order", "compression_15m_atr", "base"],
        ascending=[False, True, True, True],
        na_position="last",
    ).drop(columns="direction_order")
    return output.reset_index(drop=True)
