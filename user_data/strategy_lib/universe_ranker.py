"""Lagged, deterministic hourly ranking for the OKX V2 research universe."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class PairFeatures:
    notional_24h: float
    nonzero_ratio: float
    atr_ratio: float


@dataclass(frozen=True)
class UniverseRules:
    min_notional_24h: float = 1_000_000.0
    min_nonzero_ratio: float = 0.95
    min_atr_ratio: float = 0.002
    max_atr_ratio: float = 0.15
    target_atr_ratio: float = 0.04
    liquidity_weight: float = 0.75


def eligible_trade_symbols(rows: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(row["instId"]): str(row["symbol"])
        for row in rows
        if row.get("eligible") is True and row.get("role") == "trade"
    }


def _valid(feature: PairFeatures, rules: UniverseRules) -> bool:
    values = (feature.notional_24h, feature.nonzero_ratio, feature.atr_ratio)
    return (
        all(math.isfinite(value) for value in values)
        and feature.notional_24h >= rules.min_notional_24h
        and feature.nonzero_ratio >= rules.min_nonzero_ratio
        and rules.min_atr_ratio <= feature.atr_ratio <= rules.max_atr_ratio
    )


def scored_hour(
    snapshot: dict[str, PairFeatures], rules: UniverseRules | None = None
) -> list[tuple[str, float]]:
    active_rules = rules or UniverseRules()
    candidates = {pair: feature for pair, feature in snapshot.items() if _valid(feature, active_rules)}
    if not candidates:
        return []
    notionals = pd.Series({pair: math.log(feature.notional_24h) for pair, feature in candidates.items()})
    liquidity = notionals.rank(method="average", pct=True)
    scored: list[tuple[str, float]] = []
    for pair, feature in candidates.items():
        volatility_fit = math.exp(
            -abs(math.log(feature.atr_ratio / active_rules.target_atr_ratio))
        )
        score = (
            active_rules.liquidity_weight * float(liquidity[pair])
            + (1 - active_rules.liquidity_weight) * volatility_fit
        )
        scored.append((pair, score))
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def rank_hour(
    snapshot: dict[str, PairFeatures],
    limit: int = 30,
    rules: UniverseRules | None = None,
) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    return [pair for pair, _ in scored_hour(snapshot, rules)[:limit]]


def pair_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    source = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
    source["date"] = pd.to_datetime(source["date"], utc=True)
    previous_close = source["close"].shift(1)
    true_range = pd.concat(
        [
            source["high"] - source["low"],
            (source["high"] - previous_close).abs(),
            (source["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    source["notional_24h"] = source["quote_volume"].rolling(24, min_periods=24).sum().shift(1)
    source["nonzero_ratio"] = (
        source["volume"].gt(0).astype(float).rolling(24, min_periods=24).mean().shift(1)
    )
    source["atr_ratio"] = (
        true_range.rolling(14, min_periods=14).mean().shift(1) / source["close"].shift(1)
    )
    return source.loc[:, ["date", "notional_24h", "nonzero_ratio", "atr_ratio"]]


def build_universe_mask(
    frames: dict[str, pd.DataFrame],
    limit: int = 30,
    rules: UniverseRules | None = None,
) -> pd.DataFrame:
    feature_frames: dict[str, pd.DataFrame] = {}
    dates: set[pd.Timestamp] = set()
    for pair, frame in frames.items():
        features = pair_feature_frame(frame).set_index("date")
        feature_frames[pair] = features
        dates.update(features.dropna().index)
    rows = []
    for date in sorted(dates):
        snapshot: dict[str, PairFeatures] = {}
        for pair, features in feature_frames.items():
            if date not in features.index:
                continue
            row = features.loc[date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            snapshot[pair] = PairFeatures(
                float(row["notional_24h"]),
                float(row["nonzero_ratio"]),
                float(row["atr_ratio"]),
            )
        for rank, (pair, score) in enumerate(scored_hour(snapshot, rules)[:limit], start=1):
            rows.append(
                {"date": date, "pair": pair, "rank": rank, "score": score, "eligible": True}
            )
    return pd.DataFrame(rows, columns=["date", "pair", "rank", "score", "eligible"])
