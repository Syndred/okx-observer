"""Causal RSI(2) pullback-reversion entries inside aligned 5-minute trends."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators

DEV_SYMBOLS = (
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP", "ADA-USDT-SWAP", "LINK-USDT-SWAP", "AVAX-USDT-SWAP",
)
_STEP = pd.Timedelta(300_000_000_000, unit="ns")
_RETURN_WINDOWS = (1, 3, 6, 12, 24)
FEATURE_NAMES = (
    "side", "side_adjusted_rsi2",
    "side_return_1", "side_return_3", "side_return_6", "side_return_12", "side_return_24",
    "atr_fraction", "side_ema20_ema60_atr", "side_ema60_ema120_atr",
    "side_close_ema120_atr", "side_ema60_slope3_atr", "log_quote_volume_ratio",
    "utc_hour_sin", "utc_hour_cos",
    *(f"pair_{pair}" for pair in DEV_SYMBOLS),
)


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

    continuous = features["date"].diff().eq(_STEP).rolling(3, min_periods=3).sum().eq(3)
    signal_mask = (long_signal | short_signal) & continuous
    signal_indices = np.flatnonzero(signal_mask.to_numpy())
    available_at = features["date"] + _STEP

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


def build_event_features(
    frames: dict[str, pd.DataFrame],
    events: list[EntryEvent],
    symbols: tuple[str, ...] = DEV_SYMBOLS,
) -> np.ndarray:
    """Build a causal event matrix; all indicators end on the signal candle."""
    prepared: dict[str, tuple[pd.DataFrame, dict[int, int]]] = {}
    for pair in sorted({event.pair for event in events}):
        if pair not in frames:
            raise ValueError(f"missing candles for event pair {pair}")
        frame = frames[pair]
        if "quote_volume" not in frame.columns:
            raise ValueError("quote_volume is required for model features")
        raw = frame[["date", "open", "high", "low", "close", "quote_volume"]].copy()
        raw["date"] = pd.to_datetime(raw["date"], utc=True).dt.as_unit("ns")
        raw = raw.sort_values("date").reset_index(drop=True)
        features = add_v2_indicators(raw)
        change = features["close"].diff()
        average_gain = change.clip(lower=0).rolling(2, min_periods=2).mean()
        average_loss = -change.clip(upper=0).rolling(2, min_periods=2).mean()
        ratio = average_gain.div(average_loss.where(average_loss.gt(0)))
        rsi = 100 - 100 / (1 + ratio)
        rsi = rsi.mask(average_loss.eq(0) & average_gain.gt(0), 100)
        rsi = rsi.mask(average_gain.eq(0) & average_loss.gt(0), 0)
        rsi = rsi.mask(average_gain.eq(0) & average_loss.eq(0), 50)
        features["rsi2"] = rsi
        for window in _RETURN_WINDOWS:
            features[f"return_{window}"] = features["close"].div(
                features["close"].shift(window)
            ).sub(1)
        volume_median = features["quote_volume"].shift(1).rolling(20, min_periods=20).median()
        features["quote_volume_ratio"] = features["quote_volume"].div(volume_median)
        indices = {int(stamp.value): index
                   for index, stamp in enumerate(pd.DatetimeIndex(features["date"]))}
        prepared[pair] = features, indices

    rows: list[list[float]] = []
    for event in events:
        entry_date = pd.Timestamp(event.date)
        entry_date = (entry_date.tz_localize("UTC") if entry_date.tzinfo is None
                      else entry_date.tz_convert("UTC"))
        signal_date = entry_date - _STEP
        features, indices = prepared[event.pair]
        index = indices.get(int(signal_date.value))
        if index is None:
            raise ValueError("candidate event has no matching signal candle")
        row = features.iloc[index]
        side = 1.0 if event.side == "long" else -1.0
        atr = float(row["atr14"])
        close = float(row["close"])
        timestamp = pd.Timestamp(row["date"])
        hour = timestamp.hour + timestamp.minute / 60.0
        volume_ratio = float(row["quote_volume_ratio"])
        if not (math.isfinite(atr) and atr > 0 and math.isfinite(close) and close > 0
                and math.isfinite(volume_ratio) and volume_ratio > 0):
            raise ValueError("candidate event has incomplete model features")
        vector = [
            side,
            side * (50.0 - float(row["rsi2"])) / 50.0,
            *(side * float(row[f"return_{window}"]) for window in _RETURN_WINDOWS),
            atr / close,
            side * (float(row["ema20"]) - float(row["ema60"])) / atr,
            side * (float(row["ema60"]) - float(row["ema120"])) / atr,
            side * (close - float(row["ema120"])) / atr,
            side * float(row["ema60_slope3"]) / atr,
            math.log1p(min(volume_ratio, 20.0)),
            math.sin(2 * math.pi * hour / 24.0),
            math.cos(2 * math.pi * hour / 24.0),
            *(1.0 if event.pair == pair else 0.0 for pair in symbols),
        ]
        if len(vector) != len(FEATURE_NAMES) or not np.isfinite(vector).all():
            raise ValueError("candidate event has invalid model features")
        rows.append(vector)
    return np.asarray(rows, dtype=float).reshape((-1, len(FEATURE_NAMES)))
