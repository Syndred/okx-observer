"""Causal trend-pullback entries for historical 5-minute research."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators


@dataclass(frozen=True)
class TrendPullbackParameters:
    cadence_minutes: int = 15
    touch_atr: float = 0.25
    confirmation_body_atr: float = 0.20
    stop_buffer_atr: float = 0.10
    minimum_risk_fraction: float = 0.004
    cooldown_bars: int = 25
    quote_volume_multiple: float | None = None
    quote_volume_lookback: int = 20


def generate_events(
    pair: str,
    frame: pd.DataFrame,
    params: TrendPullbackParameters = TrendPullbackParameters(),
) -> list[EntryEvent]:
    """Trade a reclaimed EMA20 pullback inside the precomputed EMA trend.

    Signals use only closed candles. The returned event is dated at the next
    five-minute open and retains the signal-candle stop price.
    """
    if (type(params.cadence_minutes) is not int or params.cadence_minutes <= 0
            or 60 % params.cadence_minutes
            or type(params.cooldown_bars) is not int or params.cooldown_bars < 1
            or type(params.quote_volume_lookback) is not int or params.quote_volume_lookback < 1
            or any(isinstance(value, bool) or not math.isfinite(value) or value <= 0
                   for value in (params.touch_atr,
                                 params.confirmation_body_atr,
                                 params.stop_buffer_atr,
                                 params.minimum_risk_fraction))
            or (params.quote_volume_multiple is not None
                and (isinstance(params.quote_volume_multiple, bool)
                     or not isinstance(params.quote_volume_multiple, (int, float))
                     or not math.isfinite(params.quote_volume_multiple)
                     or params.quote_volume_multiple <= 0))):
        raise ValueError("invalid cadence, pullback, confirmation or stop parameters")

    required = {"date", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("candles are missing required OHLC columns")
    if params.quote_volume_multiple is not None and "quote_volume" not in frame.columns:
        raise ValueError("quote_volume is required for volume confirmation")
    columns = ["date", "open", "high", "low", "close"]
    if params.quote_volume_multiple is not None:
        columns.append("quote_volume")
    raw = frame[columns].copy()
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
    if params.quote_volume_multiple is not None:
        quote_volume = raw["quote_volume"].to_numpy(dtype=float)
        if not np.isfinite(quote_volume).all() or (quote_volume < 0).any():
            raise ValueError("candles contain invalid quote volume")

    features = add_v2_indicators(raw)
    atr = features["atr14"]
    ema20 = features["ema20"]
    ema60 = features["ema60"]
    body = features["close"] - features["open"]
    span = features["high"] - features["low"]
    if params.quote_volume_multiple is None:
        quote_volume_confirmed = pd.Series(True, index=features.index)
    else:
        baseline_quote_volume = (
            features["quote_volume"].shift(1)
            .rolling(params.quote_volume_lookback,
                     min_periods=params.quote_volume_lookback)
            .median()
        )
        quote_volume_confirmed = (
            features["quote_volume"].gt(0)
            & baseline_quote_volume.gt(0)
            & features["quote_volume"].ge(
                params.quote_volume_multiple * baseline_quote_volume
            )
        )

    touched_ema20 = (
        features["low"].le(ema20 + params.touch_atr * atr)
        & features["high"].ge(ema20 - params.touch_atr * atr)
    )
    touched_recently = touched_ema20.shift(1).rolling(3, min_periods=3).sum().ge(1)
    long_pullback_shallow = (
        features["low"] - ema60 + params.touch_atr * atr
    ).shift(1).rolling(3, min_periods=3).min().ge(0)
    short_pullback_shallow = (
        ema60 + params.touch_atr * atr - features["high"]
    ).shift(1).rolling(3, min_periods=3).min().ge(0)

    long_confirmation = (
        body.gt(0)
        & body.ge(params.confirmation_body_atr * atr)
        & features["close"].gt(features["high"].shift(1))
        & span.gt(0)
        & features["close"].ge(features["high"] - span / 3)
        & features["close"].gt(ema20)
    )
    short_confirmation = (
        body.lt(0)
        & (-body).ge(params.confirmation_body_atr * atr)
        & features["close"].lt(features["low"].shift(1))
        & span.gt(0)
        & features["close"].le(features["low"] + span / 3)
        & features["close"].lt(ema20)
    )
    long_stop = features["low"].rolling(4, min_periods=4).min() - params.stop_buffer_atr * atr
    short_stop = features["high"].rolling(4, min_periods=4).max() + params.stop_buffer_atr * atr
    long_signal = (
        features["trend_long"].fillna(False)
        & touched_recently & long_pullback_shallow & long_confirmation
        & quote_volume_confirmed
        & (features["close"] - long_stop).ge(params.minimum_risk_fraction * features["close"])
    )
    short_signal = (
        features["trend_short"].fillna(False)
        & touched_recently & short_pullback_shallow & short_confirmation
        & quote_volume_confirmed
        & (short_stop - features["close"]).ge(params.minimum_risk_fraction * features["close"])
    )

    step = pd.Timedelta(300_000_000_000, unit="ns")
    continuous = features["date"].diff().eq(step).rolling(4, min_periods=4).sum().eq(4)
    available_at = features["date"] + step
    on_schedule = available_at.dt.minute.mod(params.cadence_minutes).eq(0)
    signal_mask = (long_signal | short_signal) & continuous & on_schedule
    signal_indices = np.flatnonzero(signal_mask.to_numpy())

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
