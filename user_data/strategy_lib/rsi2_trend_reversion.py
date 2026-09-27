"""Causal RSI(2) pullback-reversion entries inside aligned 5-minute trends."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators


@dataclass(frozen=True)
class RSI2TrendReversionParameters:
    oversold: float = 10.0
    overbought: float = 90.0
    stop_atr: float = 1.5
    minimum_stop_fraction: float = 0.004
    cooldown_bars: int = 25


def generate_events(
    pair: str,
    frame: pd.DataFrame,
    params: RSI2TrendReversionParameters = RSI2TrendReversionParameters(),
) -> list[EntryEvent]:
    """Fade a two-bar RSI extreme only when the completed trend agrees."""
    numeric = (params.oversold, params.overbought, params.stop_atr,
               params.minimum_stop_fraction)
    if (any(isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) for value in numeric)
            or not 0 < params.oversold < params.overbought < 100
            or params.stop_atr <= 0 or params.minimum_stop_fraction <= 0
            or type(params.cooldown_bars) is not int or params.cooldown_bars < 1):
        raise ValueError("invalid RSI thresholds, stop or cooldown parameters")

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
    change = features["close"].diff()
    average_gain = change.clip(lower=0).rolling(2, min_periods=2).mean()
    average_loss = -change.clip(upper=0).rolling(2, min_periods=2).mean()
    ratio = average_gain.div(average_loss.where(average_loss.gt(0)))
    rsi = 100 - 100 / (1 + ratio)
    rsi = rsi.mask(average_loss.eq(0) & average_gain.gt(0), 100)
    rsi = rsi.mask(average_gain.eq(0) & average_loss.gt(0), 0)
    rsi = rsi.mask(average_gain.eq(0) & average_loss.eq(0), 50)

    long_signal = (
        features["trend_long"].fillna(False)
        & rsi.le(params.oversold)
        & rsi.shift(1).gt(params.oversold)
    )
    short_signal = (
        features["trend_short"].fillna(False)
        & rsi.ge(params.overbought)
        & rsi.shift(1).lt(params.overbought)
    )
    stop_distance = pd.concat(
        [params.stop_atr * features["atr14"],
         params.minimum_stop_fraction * features["close"]],
        axis=1,
    ).max(axis=1)
    long_stop = features["close"] - stop_distance
    short_stop = features["close"] + stop_distance

    step = pd.Timedelta(300_000_000_000, unit="ns")
    continuous = features["date"].diff().eq(step).rolling(3, min_periods=3).sum().eq(3)
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
