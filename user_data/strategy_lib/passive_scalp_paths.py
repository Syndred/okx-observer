"""Independent post-only 5m labels, not a portfolio or real execution proof.

Penetration implies hypothetical full fills: OHLC cannot establish queue position,
partial fills, or the order of intrabar prices. Orders are active for one bar only.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from user_data.strategy_lib.signal_path_analysis import _signed_move
from user_data.strategy_lib.v2_backtester import _exit_fill


@dataclass(frozen=True)
class PassiveOrder:
    """Precomputed prices known before ``date``, the first eligible 5m bar."""
    date: pd.Timestamp
    pair: str
    side: str
    limit_price: float
    stop_price: float


_COLUMNS = ["date", "close_date", "pair", "side", "entry_price", "exit_price",
            "stop_price", "target_price", "net_return", "r_multiple", "won",
            "exit_reason", "hold_minutes", "max_hold_minutes", "fees_return",
            "funding_return", "initial_risk_return", "funding_actual_return",
            "funding_imputed_return"]
_STEP_NS = pd.Timedelta(minutes=5).value


def evaluate_passive(frames, orders, target_r, hold_minutes, entry_fee=.0002,
                     exit_fee=.0005, exit_slippage=.0005, penetration=.0001,
                     funding_frames=None, missing_funding=.0001):
    """Label one-bar post-only orders with conservative stop-first exits.

    Require a complete ``[date, date + hold_minutes)`` window, even on early exit.
    A marketable opening price cancels the order. Mere limit touch does not fill
    when penetration is positive; crossing the penetration threshold assumes a
    full fill at the fixed limit. The entry candle can stop out but cannot take
    profit. Later gap stops use the worse open; all exits pay taker fees/slippage.
    Funding follows scalp_paths: no entry-bar settlement, subsequent settlements
    use bar open; absent UTC 00/08/16 payments are imputed adversely on both sides.
    Rejected and unfilled orders remain in skipped_events/skipped_counts attrs.
    R is net return divided by stop distance plus fees and exit slippage, matching
    the cost-inclusive screening convention. Concurrent exposure is not modeled.
    """
    if isinstance(target_r, bool) or not np.isfinite(target_r) or target_r <= 0:
        raise ValueError("target_r must be finite and positive")
    if type(hold_minutes) is not int or hold_minutes <= 0 or hold_minutes % 5:
        raise ValueError("hold_minutes must be a positive integer multiple of 5")
    costs = (entry_fee, exit_fee, exit_slippage, penetration, missing_funding)
    if any(isinstance(x, bool) or not np.isfinite(x) or x < 0 or x >= 1 for x in costs):
        raise ValueError("rates must be finite and in [0, 1)")
    count = hold_minutes // 5
    funding_series = {}
    for pair, frame in (funding_frames or {}).items():
        source = frame.copy()
        source["date"] = pd.to_datetime(source["date"], utc=True)
        if source["date"].isna().any() or not np.isfinite(source["rate"].to_numpy(float)).all():
            raise ValueError("actual funding must have valid dates and finite rates")
        funding_series[pair] = source.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["rate"]
    prepared = {}
    for pair, frame in frames.items():
        source = frame.copy()
        source["date"] = pd.to_datetime(source["date"], utc=True)
        source = source.sort_values("date")
        dates = pd.DatetimeIndex(source["date"])
        times = np.array([stamp.value for stamp in dates], dtype=np.int64)
        prices = source[["open", "high", "low", "close"]].to_numpy(float)
        rates = funding_series[pair].reindex(dates).to_numpy(float) if pair in funding_series else np.full(len(dates), np.nan)
        settlement = (dates.minute == 0) & (dates.hour % 8 == 0)
        prepared[pair] = times, prices, rates, settlement
    rows, skipped = [], []
    for order in orders:
        date = pd.Timestamp(order.date)
        date = date.tz_localize("UTC") if date.tzinfo is None else date.tz_convert("UTC")

        def reject(reason):
            skipped.append(dict(date=date, pair=order.pair, side=order.side, reason=reason))

        if pd.isna(date) or date.value % _STEP_NS:
            reject("invalid_date")
            continue
        if order.side not in {"long", "short"}:
            reject("invalid_side")
            continue
        sign = 1 if order.side == "long" else -1
        entry, stop = float(order.limit_price), float(order.stop_price)
        if not np.isfinite(entry) or entry <= 0:
            reject("invalid_limit")
            continue
        if not np.isfinite(stop) or stop <= 0 or sign * (entry - stop) <= 0:
            reject("invalid_stop")
            continue
        target = entry + sign * target_r * abs(entry - stop)
        if not np.isfinite(target) or target <= 0:
            reject("invalid_target")
            continue
        if order.pair not in prepared:
            reject("missing_pair")
            continue
        times, prices, rates, settlement = prepared[order.pair]
        start = int(np.searchsorted(times, date.value))
        if start == len(times) or times[start] != date.value:
            reject("missing_entry_bar")
            continue
        window = times[start:start + count]
        if len(window) != count or np.any(window != date.value + np.arange(count) * _STEP_NS):
            reject("incomplete_5m_window")
            continue
        path = prices[start:start + count]
        if (not np.isfinite(path).all() or (path <= 0).any()
                or (path[:, 1] < np.maximum(path[:, 0], path[:, 3])).any()
                or (path[:, 2] > np.minimum(path[:, 0], path[:, 3])).any()):
            reject("invalid_ohlc")
            continue
        if sign * (path[0, 0] - entry) <= 0:
            reject("marketable_at_open")
            continue
        threshold = entry * (1 - sign * penetration)
        penetrated = path[0, 2] <= threshold if sign == 1 else path[0, 1] >= threshold
        if not penetrated:
            reject("not_filled")
            continue
        stop_hits = path[:, 2] <= stop if sign == 1 else path[:, 1] >= stop
        target_hits = path[:, 1] >= target if sign == 1 else path[:, 2] <= target
        target_hits[0] = False
        hits = np.flatnonzero(stop_hits | target_hits)
        index = int(hits[0]) if len(hits) else count - 1
        if stop_hits[index]:
            raw_exit = min(stop, path[index, 0]) if sign == 1 else max(stop, path[index, 0])
            reason = "initial_stop"
        elif target_hits[index]:
            raw_exit, reason = target, f"fixed_{target_r:g}r"
        else:
            raw_exit, reason = path[index, 3], f"max_hold_{hold_minutes}m"
        close_date = date + pd.Timedelta(minutes=5 * (index + (not len(hits))))
        exit_price = _exit_fill(raw_exit, order.side, exit_slippage)
        fees = entry_fee + exit_fee * exit_price / entry
        held_rates = rates[start + 1:start + index + 1]
        held_opens = path[1:index + 1, 0]
        actual = float(np.sum(held_opens * np.where(np.isnan(held_rates), 0, held_rates * sign))) / entry
        imputed = float(np.sum(held_opens * np.where(np.isnan(held_rates), settlement[start + 1:start + index + 1] * missing_funding, 0))) / entry
        funding = actual + imputed
        net = _signed_move(order.side, entry, exit_price) / entry - fees - funding
        risk = abs(entry - stop) / entry + entry_fee + exit_fee + exit_slippage
        rows.append(dict(date=date, close_date=close_date, pair=order.pair, side=order.side,
                         entry_price=entry, exit_price=exit_price, stop_price=stop,
                         target_price=target, net_return=net, r_multiple=net / risk,
                         won=net > 0, exit_reason=reason,
                         hold_minutes=(close_date - date).total_seconds() / 60,
                         max_hold_minutes=hold_minutes, fees_return=fees,
                         funding_return=funding, initial_risk_return=risk,
                         funding_actual_return=actual, funding_imputed_return=imputed))
    result = pd.DataFrame(rows, columns=_COLUMNS)
    result.attrs["skipped_events"] = skipped
    result.attrs["skipped_counts"] = dict(Counter(row["reason"] for row in skipped))
    return result
