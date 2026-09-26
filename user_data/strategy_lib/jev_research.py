"""Causal, anonymous market features and probability evaluation for Jev research."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import math

import pandas as pd

from user_data.strategy_lib.v2_backtester import BacktestOptions, EntryEvent
from user_data.strategy_lib.v2_signal_engine import SIX_AVERAGES, add_v2_indicators


_DURATIONS = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1d"}
_RAW_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def _utc(value: object) -> pd.Timestamp:
    date = pd.Timestamp(value)
    return date.tz_localize("UTC") if date.tzinfo is None else date.tz_convert("UTC")


def _finite(value: object) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def build_state(
    event: EntryEvent,
    frames: dict[str, pd.DataFrame],
    options: BacktestOptions,
    strategy: dict,
) -> dict:
    """Only completed bars at the decision instant enter this model payload.

    ``event.date`` is the next 15-minute entry candle's opening timestamp.
    Raw columns are selected before computing indicators to ignore cached or
    potentially noncausal feature columns. Strategy must contain configuration
    only; event identity and realized returns belong outside this payload.
    """
    cutoff = _utc(event.date)
    closed = {}
    for timeframe, duration in _DURATIONS.items():
        frame = frames.get(timeframe)
        if frame is None or frame.empty:
            closed[timeframe] = pd.DataFrame(columns=_RAW_COLUMNS)
            continue
        source = frame.loc[:, _RAW_COLUMNS].copy()
        source["date"] = pd.to_datetime(source["date"], utc=True)
        source = source.loc[source["date"] + pd.Timedelta(duration) <= cutoff]
        closed[timeframe] = source.sort_values("date").drop_duplicates("date", keep="last")

    base = closed["15m"]
    if len(base) < 120:
        raise ValueError("At least 120 completed 15m candles are required")
    if base.iloc[-1]["date"] + pd.Timedelta(minutes=15) != cutoff:
        raise ValueError("The last completed 15m candle must end at event.date")
    reference = float(base.iloc[-1]["close"])
    if not math.isfinite(reference) or reference <= 0:
        raise ValueError("Last completed close must be finite and positive")
    if event.side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    stop = float(event.stop_price)
    if not math.isfinite(stop) or stop <= 0:
        raise ValueError("stop_price must be finite and positive")

    def price(value: object) -> float | None:
        number = _finite(value)
        return None if number is None else number / reference - 1.0

    timeframes = {}
    for timeframe, source in closed.items():
        if source.empty:
            timeframes[timeframe] = {"closed_bar_count": 0, "bars": [], "indicators": None}
            continue
        enriched = add_v2_indicators(source)
        tail = enriched.tail(24)
        volume_mean = float(tail["volume"].mean())
        bars = []
        for _, row in tail.iterrows():
            bar = {column: price(row[column]) for column in ("open", "high", "low", "close")}
            volume = _finite(row["volume"])
            bar["volume"] = (
                volume / volume_mean
                if volume is not None and math.isfinite(volume_mean) and volume_mean > 0
                else None
            )
            bars.append(bar)
        latest = enriched.iloc[-1]
        indicators = {column: price(latest[column]) for column in SIX_AVERAGES}
        atr = _finite(latest["atr14"])
        indicators["atr14_fraction"] = None if atr is None else atr / reference
        complete = all(indicators[column] is not None for column in SIX_AVERAGES)
        indicators["trend_long"] = bool(latest["trend_long"]) if complete else None
        indicators["trend_short"] = bool(latest["trend_short"]) if complete else None
        indicators["compression_atr"] = (
            (float(latest[list(SIX_AVERAGES)].max()) - float(latest[list(SIX_AVERAGES)].min())) / atr
            if complete and atr is not None and atr > 0 else None
        )
        timeframes[timeframe] = {
            "closed_bar_count": len(enriched), "bars": bars, "indicators": indicators,
        }

    return {
        "side": event.side,
        "stop_distance_fraction": (reference - stop) / reference if event.side == "long" else (stop - reference) / reference,
        "strategy": deepcopy(strategy),
        "execution_rules": {
            "options": asdict(options),
            "description": (
                "Predict a strictly positive realized net PnL after fees, slippage and funding. "
                "Enter on the next 15m open, adjusted adversely by slippage_rate; its price is "
                "unknown here. stop_distance_fraction describes a fixed stop relative to the "
                "last completed close: stop/reference = 1 - distance for long, 1 + distance "
                "for short. Actual initial price risk is the absolute distance between the "
                "eventual entry fill and that fixed stop, not the supplied reference-relative "
                "distance. With exit_mode=fixed3, take full profit at 3 such price-risk units. "
                "With time_mode=24h, time exit uses the close of the 15m bar opening 24 hours "
                "after entry (up to 24h15m elapsed with complete continuous candles); stop "
                "and target checks still apply within that final bar. "
                "The initial stop remains active; same-bar stop/target ambiguity is resolved "
                "conservatively in favor of the stop. The option values are authoritative."
            ),
        },
        "normalization": {
            "prices": "price / last completed 15m close - 1",
            "volume": "volume / mean volume of shown bars in the same timeframe",
            "bar_order": "oldest to newest; only fully closed bars",
        },
        "timeframes": timeframes,
    }


def event_key(event: EntryEvent) -> str:
    """Stable identifier for local joins; never include this in model state."""
    return f"{event.pair}|{_utc(event.date).isoformat()}|{event.side}"


def select_events(events: list[EntryEvent], limit: int) -> list[EntryEvent]:
    """Chronological, deterministic, evenly spread sampling including endpoints."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    ordered = sorted(events, key=lambda event: (_utc(event.date), event_key(event)))
    if limit == 0 or len(ordered) <= limit:
        return ordered
    if limit == 1:
        return [ordered[len(ordered) // 2]]
    return [ordered[index * (len(ordered) - 1) // (limit - 1)] for index in range(limit)]


def calibration(probabilities: list[float], outcomes: list[bool]) -> dict:
    """Probability quality against binary net-profit outcomes, with Wilson CI."""
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal lengths")
    ps = [float(value) for value in probabilities]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in ps):
        raise ValueError("probabilities must be finite and within [0, 1]")
    if any(value not in (0, 1, False, True) for value in outcomes):
        raise ValueError("outcomes must be binary")
    ys = [int(value) for value in outcomes]
    n = len(ps)
    bins = []
    for index in range(5):
        selected = [(p, y) for p, y in zip(ps, ys) if min(int(p * 5), 4) == index]
        bins.append({
            "lower": index / 5, "upper": (index + 1) / 5,
            "n": len(selected),
            "mean_probability": sum(p for p, _ in selected) / len(selected) if selected else None,
            "win_rate": sum(y for _, y in selected) / len(selected) if selected else None,
        })
    result = dict.fromkeys(["win_rate", "brier", "log_loss", "accuracy", "mean_probability", "wilson95"])
    result.update(n=n, calibration_bins=bins)
    if not n:
        return result
    rate = sum(ys) / n
    z = 1.959963984540054
    denominator = 1 + z * z / n
    center = (rate + z * z / (2 * n)) / denominator
    half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / denominator
    clipped = [min(max(p, 1e-15), 1 - 1e-15) for p in ps]
    result.update(
        win_rate=rate,
        brier=sum((p - y) ** 2 for p, y in zip(ps, ys)) / n,
        log_loss=-sum(y * math.log(p) + (1 - y) * math.log1p(-p) for p, y in zip(clipped, ys)) / n,
        accuracy=sum((p >= 0.5) == bool(y) for p, y in zip(ps, ys)) / n,
        mean_probability=sum(ps) / n,
        wilson95={"low": max(0.0, center - half), "high": min(1.0, center + half)},
    )
    return result
