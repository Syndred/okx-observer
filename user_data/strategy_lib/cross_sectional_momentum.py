"""Causal 5-minute cross-sectional momentum entries for research only."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_backtester import EntryEvent
from user_data.strategy_lib.v2_signal_engine import add_v2_indicators


@dataclass(frozen=True)
class CrossSectionalMomentumParameters:
    lookback_bars: int = 12
    rebalance_bars: int = 6
    minimum_spread: float = 0.006
    stop_atr: float = 1.5


def generate_events(
    frames: dict[str, pd.DataFrame],
    params: CrossSectionalMomentumParameters = CrossSectionalMomentumParameters(),
) -> list[EntryEvent]:
    """Rank symbols on completed returns; schedule entries at the next bar open.

    Every ``rebalance_bars`` candles, long the strongest symbol and short the
    weakest when their lookback-return spread reaches ``minimum_spread``. Ties
    use symbol order. Stops use only the decision candle's close and ATR.
    """
    if len(frames) < 2:
        raise ValueError("at least two symbols are required")
    if (type(params.lookback_bars) is not int or params.lookback_bars <= 0
            or type(params.rebalance_bars) is not int or params.rebalance_bars <= 0
            or isinstance(params.minimum_spread, bool)
            or not math.isfinite(params.minimum_spread) or params.minimum_spread < 0
            or isinstance(params.stop_atr, bool) or not math.isfinite(params.stop_atr)
            or params.stop_atr <= 0):
        raise ValueError("invalid lookback, rebalance interval, spread or stop")
    interval_minutes = params.rebalance_bars * 5
    if interval_minutes > 60 or 60 % interval_minutes:
        raise ValueError("rebalance interval must divide one hour")

    returns: dict[str, pd.Series] = {}
    closes: dict[str, pd.Series] = {}
    atrs: dict[str, pd.Series] = {}
    for pair, frame in sorted(frames.items()):
        required = {"date", "open", "high", "low", "close"}
        if not required.issubset(frame.columns):
            raise ValueError(f"{pair} is missing required candle columns")
        raw = frame[["date", "open", "high", "low", "close"]].copy()
        raw["date"] = pd.to_datetime(raw["date"], utc=True).dt.as_unit("ns")
        raw = raw.sort_values("date").reset_index(drop=True)
        if raw.empty or raw["date"].duplicated().any():
            raise ValueError(f"{pair} has no candles or duplicate dates")
        if not raw["date"].eq(raw["date"].dt.floor("5min")).all():
            raise ValueError(f"{pair} candle dates must align to five minutes")
        values = raw[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if (not np.isfinite(values).all() or (values <= 0).any()
                or (raw["high"] < raw[["open", "low", "close"]].max(axis=1)).any()
                or (raw["low"] > raw[["open", "high", "close"]].min(axis=1)).any()):
            raise ValueError(f"{pair} has invalid OHLC values")
        features = add_v2_indicators(raw)
        index = pd.DatetimeIndex(features["date"])
        close = features["close"].astype(float)
        returns[pair] = pd.Series(
            close.div(close.shift(params.lookback_bars)).sub(1).to_numpy(), index=index
        )
        closes[pair] = pd.Series(close.to_numpy(), index=index)
        atrs[pair] = pd.Series(features["atr14"].to_numpy(dtype=float), index=index)

    return_matrix = pd.concat(returns, axis=1, join="inner").sort_index()
    close_matrix = pd.concat(closes, axis=1, join="inner").reindex(return_matrix.index)
    atr_matrix = pd.concat(atrs, axis=1, join="inner").reindex(return_matrix.index)
    names = sorted(frames)
    events: list[EntryEvent] = []
    for decision_at, row in return_matrix.iterrows():
        available_at = pd.Timestamp(decision_at.value + 5 * 60 * 1_000_000_000,
                                    unit="ns", tz="UTC")
        if available_at.minute % interval_minutes != 0:
            continue
        values = row.reindex(names)
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            continue
        spread = float(values.max() - values.min())
        if spread < params.minimum_spread:
            continue
        high_pair = min(names, key=lambda pair: (-float(values[pair]), pair))
        low_pair = min(names, key=lambda pair: (float(values[pair]), pair))
        if high_pair == low_pair:
            continue
        high_close = float(close_matrix.at[decision_at, high_pair])
        high_atr = float(atr_matrix.at[decision_at, high_pair])
        low_close = float(close_matrix.at[decision_at, low_pair])
        low_atr = float(atr_matrix.at[decision_at, low_pair])
        if not all(math.isfinite(value) and value > 0 for value in
                   (high_close, high_atr, low_close, low_atr)):
            continue
        events.extend((
            EntryEvent(available_at, high_pair, "long",
                       high_close - params.stop_atr * high_atr, 1),
            EntryEvent(available_at, low_pair, "short",
                       low_close + params.stop_atr * low_atr, 1),
        ))
    return sorted(events, key=lambda event: (event.date, event.pair, event.side))
