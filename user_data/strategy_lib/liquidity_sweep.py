"""Causal range-boundary sweep and reclaim signals for 5-minute research."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators


@dataclass(frozen=True)
class LiquiditySweepParameters:
    lookback_bars: int = 24
    sweep_atr: float = 0.20
    confirmation_body_atr: float = 0.30
    stop_buffer_atr: float = 0.10
    minimum_risk_fraction: float = 0.004
    cooldown_bars: int = 13


def generate_events(
    pair: str,
    frame: pd.DataFrame,
    params: LiquiditySweepParameters = LiquiditySweepParameters(),
) -> list[EntryEvent]:
    """Fade a completed-candle sweep that closes back inside the prior range."""
    if (type(params.lookback_bars) is not int or params.lookback_bars < 2
            or type(params.cooldown_bars) is not int or params.cooldown_bars < 1
            or any(isinstance(value, bool) or not math.isfinite(value) or value <= 0
                   for value in (params.sweep_atr,
                                 params.confirmation_body_atr,
                                 params.stop_buffer_atr,
                                 params.minimum_risk_fraction))):
        raise ValueError("invalid lookback, sweep, confirmation, stop or cooldown parameters")

    required = {"date", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("candles are missing required OHLC columns")
    raw = frame[["date", "open", "high", "low", "close"]].copy()
    raw["date"] = pd.to_datetime(raw["date"], utc=True).dt.as_unit("ns")
    raw = raw.sort_values("date").reset_index(drop=True)
    if raw.empty or raw["date"].duplicated().any():
        raise ValueError("candles must be nonempty and have unique dates")
    if not raw["date"].eq(raw["date"].dt.floor("5min")).all():
        raise ValueError("candles must align to five-minute boundaries")
    prices = raw[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if (not np.isfinite(prices).all() or (prices <= 0).any()
            or (raw["high"] < raw[["open", "low", "close"]].max(axis=1)).any()
            or (raw["low"] > raw[["open", "high", "close"]].min(axis=1)).any()):
        raise ValueError("candles contain invalid OHLC values")

    features = add_v2_indicators(raw)
    atr = features["atr14"]
    prior_high = (
        features["high"].shift(1)
        .rolling(params.lookback_bars, min_periods=params.lookback_bars)
        .max()
    )
    prior_low = (
        features["low"].shift(1)
        .rolling(params.lookback_bars, min_periods=params.lookback_bars)
        .min()
    )
    body = features["close"] - features["open"]
    span = features["high"] - features["low"]
    long_sweep = (
        features["low"].le(prior_low - params.sweep_atr * atr)
        & features["close"].gt(prior_low)
        & body.ge(params.confirmation_body_atr * atr)
        & span.gt(0)
        & features["close"].ge(features["high"] - span / 3)
    )
    short_sweep = (
        features["high"].ge(prior_high + params.sweep_atr * atr)
        & features["close"].lt(prior_high)
        & (-body).ge(params.confirmation_body_atr * atr)
        & span.gt(0)
        & features["close"].le(features["low"] + span / 3)
    )
    long_stop = features["low"] - params.stop_buffer_atr * atr
    short_stop = features["high"] + params.stop_buffer_atr * atr
    long_signal = (
        long_sweep & ~short_sweep
        & (features["close"] - long_stop).ge(params.minimum_risk_fraction * features["close"])
    )
    short_signal = (
        short_sweep & ~long_sweep
        & (short_stop - features["close"]).ge(params.minimum_risk_fraction * features["close"])
    )

    step = pd.Timedelta(300_000_000_000, unit="ns")
    continuous = features["date"].diff().eq(step).rolling(
        params.lookback_bars, min_periods=params.lookback_bars
    ).sum().eq(params.lookback_bars)
    signal_mask = (long_signal | short_signal) & continuous
    signal_indices = np.flatnonzero(signal_mask.to_numpy())
    available_at = features["date"] + step

    events: list[EntryEvent] = []
    next_allowed = 0
    for index in signal_indices:
        if index < next_allowed:
            continue
        side = "long" if bool(long_signal.iloc[index]) else "short"
        stop = float(long_stop.iloc[index] if side == "long" else short_stop.iloc[index])
        events.append(EntryEvent(available_at.iloc[index], pair, side, stop, 1))
        next_allowed = index + params.cooldown_bars
    return events
