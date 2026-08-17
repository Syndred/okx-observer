"""Causal consecutive-move and volume-expansion screener."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from user_data.strategy_lib.v2_signal_engine import add_v2_indicators


STATUS_ORDER = {
    "momentum_up": 0,
    "momentum_down": 1,
    "volume_spike_up": 2,
    "volume_spike_down": 3,
    "four_hour_up": 4,
    "four_hour_down": 5,
    "watch_up": 6,
    "watch_down": 7,
    "intraday_up": 8,
    "intraday_down": 9,
    "insufficient": 10,
    "listed_less_than_30_days": 11,
    "reference_only": 12,
    "not_live": 13,
    "unknown_category": 14,
    "none": 15,
}

CANDIDATE_STATUSES = {
    "momentum_up",
    "momentum_down",
    "volume_spike_up",
    "volume_spike_down",
    "four_hour_up",
    "four_hour_down",
}


@dataclass(frozen=True)
class MomentumParameters:
    min_age_days: int = 30
    min_daily_streak: int = 2
    min_4h_streak: int = 3
    volume_lookback: int = 20
    volume_expand_mult: float = 2.0
    atr_expand_mult: float = 1.5
    ticker_change_alert: float = 0.05
    min_history_bars: int = 35


def _validate_parameters(params: MomentumParameters) -> None:
    if (
        params.min_age_days < 0
        or params.min_daily_streak < 1
        or params.min_4h_streak < 1
        or params.volume_lookback < 2
        or params.volume_expand_mult <= 1
        or params.atr_expand_mult <= 1
        or params.ticker_change_alert <= 0
        or params.min_history_bars < params.volume_lookback + 2
    ):
        raise ValueError("momentum thresholds must be positive and bounded")


def close_to_close_streak(closes: np.ndarray) -> tuple[int, str]:
    values = np.asarray(closes, dtype=float)
    if values.size < 2 or not np.isfinite(values[-1]) or not np.isfinite(values[-2]):
        return 0, "none"
    diffs = np.diff(values)
    last = diffs[-1]
    if last > 0:
        direction = "long"
        wanted = diffs > 0
    elif last < 0:
        direction = "short"
        wanted = diffs < 0
    else:
        return 0, "none"
    streak = 0
    for flag in reversed(wanted.tolist()):
        if not flag:
            break
        streak += 1
    return streak, direction


def volume_ratio(quote_volume: np.ndarray, lookback: int) -> float:
    values = np.asarray(quote_volume, dtype=float)
    if values.size < lookback + 1:
        return float("nan")
    current = values[-1]
    baseline = values[-(lookback + 1) : -1]
    if not np.isfinite(current) or not np.isfinite(baseline).all():
        return float("nan")
    mean = float(baseline.mean())
    if mean <= 0:
        return float("nan")
    return float(current / mean)


def _atr_ratio(frame: pd.DataFrame) -> float:
    if frame.empty or len(frame) < 15:
        return float("nan")
    source = add_v2_indicators(frame)
    row = source.iloc[-1]
    atr = float(row.get("atr14", np.nan))
    high = float(row["high"])
    low = float(row["low"])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(high) or not np.isfinite(low):
        return float("nan")
    return float((high - low) / atr)


def _change_pct(closes: np.ndarray) -> float:
    if closes.size < 2 or not np.isfinite(closes[-1]) or not np.isfinite(closes[-2]) or closes[-2] == 0:
        return float("nan")
    return float((closes[-1] - closes[-2]) / closes[-2])


def _ticker_change(ticker: dict[str, object] | None) -> float:
    if not ticker:
        return float("nan")
    try:
        last = float(ticker.get("last", np.nan))
        open24h = float(ticker.get("open24h", np.nan))
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(last) or not np.isfinite(open24h) or open24h <= 0:
        return float("nan")
    return float((last - open24h) / open24h)


def _expanded(volume: float, atr: float, params: MomentumParameters) -> bool:
    return (np.isfinite(volume) and volume >= params.volume_expand_mult) or (
        np.isfinite(atr) and atr >= params.atr_expand_mult
    )


def _classify(
    daily_streak: int,
    daily_direction: str,
    four_hour_streak: int,
    four_hour_direction: str,
    daily_volume: float,
    daily_atr: float,
    four_hour_volume: float,
    four_hour_atr: float,
    change_24h: float,
    params: MomentumParameters,
) -> str:
    daily_expanded = _expanded(daily_volume, daily_atr, params)
    four_hour_expanded = _expanded(four_hour_volume, four_hour_atr, params)
    if daily_streak >= params.min_daily_streak and daily_direction == "long" and daily_expanded:
        return "momentum_up"
    if daily_streak >= params.min_daily_streak and daily_direction == "short" and daily_expanded:
        return "momentum_down"
    if daily_streak == 1 and daily_direction == "long" and daily_expanded:
        return "volume_spike_up"
    if daily_streak == 1 and daily_direction == "short" and daily_expanded:
        return "volume_spike_down"
    if (
        daily_streak < params.min_daily_streak
        and four_hour_streak >= params.min_4h_streak
        and four_hour_direction == "long"
        and four_hour_expanded
    ):
        return "four_hour_up"
    if (
        daily_streak < params.min_daily_streak
        and four_hour_streak >= params.min_4h_streak
        and four_hour_direction == "short"
        and four_hour_expanded
    ):
        return "four_hour_down"
    if daily_streak >= params.min_daily_streak and daily_direction == "long":
        return "watch_up"
    if daily_streak >= params.min_daily_streak and daily_direction == "short":
        return "watch_down"
    if np.isfinite(change_24h) and change_24h >= params.ticker_change_alert:
        return "intraday_up"
    if np.isfinite(change_24h) and change_24h <= -params.ticker_change_alert:
        return "intraday_down"
    return "none"


def _score(
    daily_streak: int,
    four_hour_streak: int,
    volume: float,
    atr: float,
    change_24h: float,
) -> float:
    score = daily_streak * 10.0 + four_hour_streak * 2.0
    if np.isfinite(volume):
        score += min(volume, 10.0) * 5.0
    if np.isfinite(atr):
        score += min(atr, 5.0) * 3.0
    if np.isfinite(change_24h):
        score += abs(change_24h) * 100.0
    return float(score)


def _empty_snapshot(status: str) -> dict[str, object]:
    return {
        "status": status,
        "direction": "none",
        "candidate": False,
        "daily_streak": 0,
        "four_hour_streak": 0,
        "volume_ratio": float("nan"),
        "atr_ratio": float("nan"),
        "four_hour_volume_ratio": float("nan"),
        "four_hour_atr_ratio": float("nan"),
        "daily_change_pct": float("nan"),
        "change_24h": float("nan"),
        "score": 0.0,
        "last_price": float("nan"),
        "quote_volume": float("nan"),
        "daily_candle": pd.NaT,
        "four_hour_candle": pd.NaT,
    }


def momentum_snapshot(
    daily: pd.DataFrame,
    four_hour: pd.DataFrame,
    ticker: dict[str, object] | None = None,
    params: MomentumParameters | None = None,
) -> dict[str, object]:
    """Classify one contract from confirmed daily/4H candles plus an optional ticker."""
    active = params or MomentumParameters()
    _validate_parameters(active)
    if daily.empty or len(daily) < active.min_history_bars:
        return _empty_snapshot("insufficient")

    daily_sorted = daily.sort_values("date").drop_duplicates("date", keep="last")
    closes = daily_sorted["close"].to_numpy(dtype=float)
    quote = (
        daily_sorted["quote_volume"]
        if "quote_volume" in daily_sorted
        else daily_sorted["volume"]
    ).to_numpy(dtype=float)
    daily_streak, daily_direction = close_to_close_streak(closes)
    daily_volume = volume_ratio(quote, active.volume_lookback)
    daily_atr = _atr_ratio(daily_sorted)
    daily_change = _change_pct(closes)
    change_24h = _ticker_change(ticker)

    four_hour_streak = 0
    four_hour_direction = "none"
    four_hour_volume = float("nan")
    four_hour_atr = float("nan")
    four_hour_candle = pd.NaT
    if not four_hour.empty and len(four_hour) >= active.volume_lookback + 2:
        hour_sorted = four_hour.sort_values("date").drop_duplicates("date", keep="last")
        hour_closes = hour_sorted["close"].to_numpy(dtype=float)
        hour_quote = (
            hour_sorted["quote_volume"]
            if "quote_volume" in hour_sorted
            else hour_sorted["volume"]
        ).to_numpy(dtype=float)
        four_hour_streak, four_hour_direction = close_to_close_streak(hour_closes)
        four_hour_volume = volume_ratio(hour_quote, active.volume_lookback)
        four_hour_atr = _atr_ratio(hour_sorted)
        four_hour_candle = pd.Timestamp(hour_sorted["date"].iloc[-1])

    status = _classify(
        daily_streak,
        daily_direction,
        four_hour_streak,
        four_hour_direction,
        daily_volume,
        daily_atr,
        four_hour_volume,
        four_hour_atr,
        change_24h,
        active,
    )
    direction = {
        "momentum_up": "long",
        "volume_spike_up": "long",
        "four_hour_up": "long",
        "watch_up": "long",
        "intraday_up": "long",
        "momentum_down": "short",
        "volume_spike_down": "short",
        "four_hour_down": "short",
        "watch_down": "short",
        "intraday_down": "short",
    }.get(status, "none")
    last_price = float(closes[-1])
    if ticker and np.isfinite(_ticker_change(ticker)):
        last_price = float(ticker["last"])
    return {
        "status": status,
        "direction": direction,
        "candidate": status in CANDIDATE_STATUSES,
        "daily_streak": int(daily_streak),
        "four_hour_streak": int(four_hour_streak),
        "volume_ratio": float(daily_volume),
        "atr_ratio": float(daily_atr),
        "four_hour_volume_ratio": float(four_hour_volume),
        "four_hour_atr_ratio": float(four_hour_atr),
        "daily_change_pct": float(daily_change),
        "change_24h": float(change_24h),
        "score": _score(daily_streak, four_hour_streak, daily_volume, daily_atr, change_24h),
        "last_price": last_price,
        "quote_volume": float(quote[-1]) if quote.size else float("nan"),
        "daily_candle": pd.Timestamp(daily_sorted["date"].iloc[-1]),
        "four_hour_candle": four_hour_candle,
    }


def screen_instruments(
    instruments: list[dict[str, object]],
    daily_frames: dict[str, pd.DataFrame],
    four_hour_frames: dict[str, pd.DataFrame],
    tickers: dict[str, dict[str, object]] | None = None,
    params: MomentumParameters | None = None,
) -> pd.DataFrame:
    """Rank live contracts; candidates have a confirmed expansion, not just a ticker pop."""
    active = params or MomentumParameters()
    _validate_parameters(active)
    ticker_map = tickers or {}
    rows: list[dict[str, object]] = []
    for instrument in instruments:
        instrument_id = str(instrument["instId"])
        role = str(instrument.get("role") or "trade")
        reason = str(instrument.get("eligibility_reason") or "eligible")
        if role == "reference":
            snapshot = _empty_snapshot("reference_only")
        elif reason != "eligible":
            snapshot = _empty_snapshot(reason)
        else:
            snapshot = momentum_snapshot(
                daily_frames.get(instrument_id, pd.DataFrame()),
                four_hour_frames.get(instrument_id, pd.DataFrame()),
                ticker_map.get(instrument_id),
                active,
            )
        rows.append(
            {
                "instrument": instrument_id,
                "base": str(instrument.get("base") or instrument_id.split("-")[0]),
                "role": role,
                "age_days": int(instrument.get("ageDays") or 0),
                "inst_category": instrument.get("instCategory", ""),
                **snapshot,
            }
        )
    output = pd.DataFrame(rows)
    if output.empty:
        return output
    output["status_order"] = output["status"].map(STATUS_ORDER).fillna(99)
    output = output.sort_values(
        ["candidate", "status_order", "score", "instrument"],
        ascending=[False, True, False, True],
        kind="stable",
    ).drop(columns="status_order")
    return output.reset_index(drop=True)
