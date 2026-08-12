"""Historical daily/4H trend plus 15m six-average compression signals."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import (
    SIX_AVERAGES,
    _universe_hours,
    add_v2_indicators,
)


@dataclass(frozen=True)
class TrendCompressionParameters:
    compression_15m_atr: float = 2.0
    stop_buffer_atr: float = 0.20
    trend_mode: str = "basic"


def _trend_informative(
    frame: pd.DataFrame, timeframe: str, prefix: str, trend_mode: str
) -> pd.DataFrame:
    source = add_v2_indicators(frame)
    source["date"] = pd.to_datetime(source["date"], utc=True).dt.as_unit("ns")
    close = source["close"].astype(float)
    basic_long = (
        (close > source["ema20"])
        & (source["ema20"] > source["ema60"])
        & (source["ema60_slope3"] > 0)
    )
    basic_short = (
        (close < source["ema20"])
        & (source["ema20"] < source["ema60"])
        & (source["ema60_slope3"] < 0)
    )
    if trend_mode == "strict_six":
        source["active_long"] = (
            basic_long
            & (close > source.loc[:, SIX_AVERAGES].max(axis=1))
            & (source["ma20"] > source["ma60"])
            & (source["ma60"] > source["ma120"])
            & (source["ema60"] > source["ema120"])
        )
        source["active_short"] = (
            basic_short
            & (close < source.loc[:, SIX_AVERAGES].min(axis=1))
            & (source["ma20"] < source["ma60"])
            & (source["ma60"] < source["ma120"])
            & (source["ema60"] < source["ema120"])
        )
    elif trend_mode == "basic":
        source["active_long"] = basic_long
        source["active_short"] = basic_short
    else:
        raise ValueError(f"unsupported trend_mode: {trend_mode}")
    period = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}[timeframe]
    source["available_at"] = source["date"] + period
    return source.loc[
        :, ["available_at", "date", "active_long", "active_short"]
    ].rename(
        columns={
            "date": f"{prefix}_date",
            "active_long": f"{prefix}_long",
            "active_short": f"{prefix}_short",
        }
    ).sort_values("available_at")


def scan_trend_compression_setups(
    *,
    pair: str,
    candles15m: pd.DataFrame,
    candles4h: pd.DataFrame,
    candles1d: pd.DataFrame,
    universe_mask: pd.DataFrame | None,
    params: TrendCompressionParameters,
) -> pd.DataFrame:
    """Signal once when an aligned-trend contract enters a 15m compression episode."""
    if params.compression_15m_atr <= 0 or params.stop_buffer_atr < 0:
        raise ValueError("compression must be positive and stop buffer non-negative")
    original_index = candles15m.index
    base = add_v2_indicators(candles15m)
    base["date"] = pd.to_datetime(base["date"], utc=True).dt.as_unit("ns")
    base["cluster_high"] = base.loc[:, SIX_AVERAGES].max(axis=1)
    base["cluster_low"] = base.loc[:, SIX_AVERAGES].min(axis=1)
    base["compression"] = (
        base["cluster_high"] - base["cluster_low"]
    ) / base["atr14"]
    for frame, timeframe, prefix in (
        (candles1d, "1d", "daily"),
        (candles4h, "4h", "four_hour"),
    ):
        base = pd.merge_asof(
            base.sort_values("date"),
            _trend_informative(frame, timeframe, prefix, params.trend_mode),
            left_on="date",
            right_on="available_at",
            direction="backward",
            allow_exact_matches=True,
        ).drop(columns="available_at")
    if universe_mask is None:
        base["in_universe"] = True
    else:
        selected_hours = _universe_hours(universe_mask, pair)
        base["in_universe"] = base["date"].dt.floor("h").isin(selected_hours)
    valid = (
        np.isfinite(base["atr14"])
        & (base["atr14"] > 0)
        & np.isfinite(base["compression"])
    )
    compressed = valid & (base["compression"] <= params.compression_15m_atr)
    long_eligible = (
        compressed
        & base["daily_long"].fillna(False)
        & base["four_hour_long"].fillna(False)
        & base["in_universe"]
    )
    short_eligible = (
        compressed
        & base["daily_short"].fillna(False)
        & base["four_hour_short"].fillna(False)
        & base["in_universe"]
    )
    base["enter_long"] = (long_eligible & ~long_eligible.shift(1, fill_value=False)).astype(int)
    base["enter_short"] = (
        short_eligible & ~short_eligible.shift(1, fill_value=False)
    ).astype(int)
    base["initial_stop_price"] = np.nan
    long_rows = base["enter_long"] == 1
    short_rows = base["enter_short"] == 1
    base.loc[long_rows, "initial_stop_price"] = (
        base.loc[long_rows, "cluster_low"]
        - params.stop_buffer_atr * base.loc[long_rows, "atr14"]
    )
    base.loc[short_rows, "initial_stop_price"] = (
        base.loc[short_rows, "cluster_high"]
        + params.stop_buffer_atr * base.loc[short_rows, "atr14"]
    )
    base.index = original_index
    return base
