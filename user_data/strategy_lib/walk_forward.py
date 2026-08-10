"""Deterministic walk-forward windows, candidate grid, and hard-gate scoring."""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import date
from itertools import product
import math
from typing import Iterable

from user_data.strategy_lib.v2_signal_engine import V2Parameters


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date


@dataclass(frozen=True)
class CandidateScore:
    passed: bool
    score: float
    profit_factor: float
    max_drawdown: float
    trades: int
    worst_window_pf: float
    stress_pf: float
    failed_gates: tuple[str, ...]


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def rolling_windows(
    start: date,
    end: date,
    train_months: int = 12,
    validation_months: int = 3,
    step_months: int = 3,
) -> list[WalkForwardWindow]:
    if min(train_months, validation_months, step_months) <= 0:
        raise ValueError("window lengths must be positive")
    windows: list[WalkForwardWindow] = []
    train_start = start
    while True:
        train_end = _add_months(train_start, train_months)
        validation_end = _add_months(train_end, validation_months)
        if validation_end > end:
            break
        windows.append(
            WalkForwardWindow(train_start, train_end, train_end, validation_end)
        )
        train_start = _add_months(train_start, step_months)
    return windows


def candidate_grid(smoke: bool = False) -> list[V2Parameters]:
    if smoke:
        return [V2Parameters(), V2Parameters(compression_atr=0.8, breakout_atr=0.2)]
    return [
        V2Parameters(
            compression_atr=compression,
            breakout_atr=breakout,
            pullback_atr=pullback,
            pullback_wait_15m=wait,
            strict_market_consensus=strict,
        )
        for compression, breakout, pullback, wait, strict in product(
            (0.4, 0.6, 0.8, 1.0),
            (0.05, 0.10, 0.20, 0.30),
            (0.10, 0.20, 0.30),
            (4, 8, 12),
            (False, True),
        )
    ]


def parameter_dict(params: V2Parameters) -> dict[str, object]:
    return asdict(params)


def candidate_score(window_metrics: Iterable[dict[str, float]]) -> CandidateScore:
    metrics = list(window_metrics)
    if not metrics:
        raise ValueError("at least one validation window is required")
    trades = sum(int(metric.get("trades", 0)) for metric in metrics)
    if all("gross_profit" in metric and "gross_loss" in metric for metric in metrics):
        gross_profit = sum(float(metric["gross_profit"]) for metric in metrics)
        gross_loss = sum(float(metric["gross_loss"]) for metric in metrics)
        profit_factor = math.inf if gross_loss == 0 and gross_profit > 0 else (
            gross_profit / gross_loss if gross_loss > 0 else 0.0
        )
    else:
        weights = [max(1, int(metric.get("trades", 0))) for metric in metrics]
        profit_factor = sum(
            float(metric.get("pf", 0)) * weight
            for metric, weight in zip(metrics, weights)
        ) / sum(weights)
    max_drawdown = max(float(metric.get("drawdown", math.inf)) for metric in metrics)
    worst_window_pf = min(float(metric.get("pf", 0)) for metric in metrics)
    stress_pf = min(float(metric.get("stress_pf", 0)) for metric in metrics)
    failed: list[str] = []
    if profit_factor < 1.15:
        failed.append("profit_factor")
    if max_drawdown > 0.35:
        failed.append("max_drawdown")
    if trades < 300:
        failed.append("trades")
    if worst_window_pf < 0.95:
        failed.append("worst_window_pf")
    if stress_pf < 1.05:
        failed.append("stress_pf")
    score = (
        min(profit_factor, 3.0)
        + min(worst_window_pf, 2.0)
        + min(stress_pf, 2.0)
        - 2 * max_drawdown
        + min(math.log10(max(trades, 1)) / 10, 0.5)
    )
    return CandidateScore(
        passed=not failed,
        score=score,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        trades=trades,
        worst_window_pf=worst_window_pf,
        stress_pf=stress_pf,
        failed_gates=tuple(failed),
    )
