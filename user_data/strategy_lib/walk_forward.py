"""Deterministic walk-forward windows, candidate grid, and hard-gate scoring."""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import date
from itertools import product
import math
from typing import Iterable

from user_data.strategy_lib.v2_signal_engine import V2Parameters
from user_data.strategy_lib.dual_ma_signal_engine import DualMaParameters
from user_data.strategy_lib.six_ma_mtf_signal_engine import SixMaMtfParameters


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


def candidate_grid(smoke: bool = False, exhaustive: bool = False) -> list[V2Parameters]:
    if smoke:
        return [V2Parameters(), V2Parameters(compression_atr=0.8, breakout_atr=0.2)]
    full = [
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
    if exhaustive:
        return full
    selected = [V2Parameters()]
    for value in (0.4, 0.6, 0.8, 1.0):
        selected.append(V2Parameters(compression_atr=value))
    for value in (0.05, 0.10, 0.20, 0.30):
        selected.append(V2Parameters(breakout_atr=value))
    for value in (0.10, 0.20, 0.30):
        selected.append(V2Parameters(pullback_atr=value))
    for value in (4, 8, 12):
        selected.append(V2Parameters(pullback_wait_15m=value))
    selected.append(V2Parameters(strict_market_consensus=True))
    unique = list(dict.fromkeys(selected))
    for index in range(64):
        candidate = full[round(index * (len(full) - 1) / 63)]
        if candidate not in unique:
            unique.append(candidate)
        if len(unique) >= 32:
            break
    return unique


def dual_ma_candidate_grid(
    smoke: bool = False, exhaustive: bool = False
) -> list[DualMaParameters]:
    if smoke:
        return [
            DualMaParameters(),
            DualMaParameters(fast_period=12, slow_period=26, pyramid_enabled=False),
        ]
    pairs = ((9, 21), (12, 26), (20, 60), (10, 30))
    full = [
        DualMaParameters(
            fast_period=fast,
            slow_period=slow,
            pullback_atr=pullback,
            pullback_wait_15m=wait,
            strict_market_consensus=strict,
            require_4h_trend=require_4h,
            pyramid_enabled=pyramid,
            pyramid_trigger_r=trigger,
            pyramid_risk_fraction=fraction,
            max_pyramids=adds,
        )
        for fast, slow in pairs
        for pullback in (0.20, 0.30, 0.50)
        for wait in (4, 8, 12)
        for strict in (False, True)
        for require_4h in (True, False)
        for pyramid in (True, False)
        for trigger in (1.0, 1.5)
        for fraction in (0.35, 0.50)
        for adds in (1, 2)
    ]
    if exhaustive:
        return full
    selected = [DualMaParameters()]
    for fast, slow in pairs:
        selected.append(DualMaParameters(fast_period=fast, slow_period=slow))
    for pullback in (0.20, 0.30, 0.50):
        selected.append(DualMaParameters(pullback_atr=pullback))
    for wait in (4, 8, 12):
        selected.append(DualMaParameters(pullback_wait_15m=wait))
    selected.append(DualMaParameters(strict_market_consensus=True))
    selected.append(DualMaParameters(require_4h_trend=False))
    selected.append(DualMaParameters(pyramid_enabled=False))
    selected.append(DualMaParameters(pyramid_trigger_r=1.5, max_pyramids=2))
    unique = list(dict.fromkeys(selected))
    for index in range(80):
        candidate = full[round(index * (len(full) - 1) / 79)]
        if candidate not in unique:
            unique.append(candidate)
        if len(unique) >= 32:
            break
    return unique


def six_ma_mtf_candidate_grid(
    smoke: bool = False, exhaustive: bool = False
) -> list[SixMaMtfParameters]:
    if smoke:
        return [
            SixMaMtfParameters(),
            SixMaMtfParameters(
                compression_4h_atr=2.0,
                compression_15m_atr=2.0,
                breakout_4h_atr=0.05,
                breakout_15m_atr=0.05,
                zone_max_age_4h=12,
                setup_wait_15m=48,
                require_full_4h_trend=False,
            ),
            SixMaMtfParameters(entry_trigger="pullback_rejection"),
            SixMaMtfParameters(entry_trigger="compression_close"),
            SixMaMtfParameters(
                compression_4h_atr=2.0,
                compression_15m_atr=2.0,
                breakout_4h_atr=0.05,
                breakout_15m_atr=0.05,
                zone_max_age_4h=12,
                setup_wait_15m=48,
                entry_trigger="pullback_rejection",
                require_full_4h_trend=False,
            ),
            SixMaMtfParameters(
                compression_4h_atr=2.0,
                compression_15m_atr=2.0,
                breakout_4h_atr=0.05,
                breakout_15m_atr=0.05,
                zone_max_age_4h=12,
                setup_wait_15m=48,
                entry_trigger="compression_close",
                require_full_4h_trend=False,
            ),
        ]
    full = [
        SixMaMtfParameters(
            compression_4h_atr=compression_4h,
            breakout_4h_atr=breakout_4h,
            compression_15m_atr=compression_15m,
            breakout_15m_atr=breakout_15m,
            stop_buffer_atr=stop_buffer,
            zone_max_age_4h=zone_age,
            setup_wait_15m=wait,
            entry_trigger=entry_trigger,
            require_full_4h_trend=require_full_trend,
            strict_market_consensus=strict,
        )
        for compression_4h, breakout_4h, compression_15m, breakout_15m, stop_buffer, zone_age, wait, entry_trigger, require_full_trend, strict in product(
            (0.8, 1.2, 1.6, 2.0),
            (0.05, 0.10),
            (0.8, 1.4, 2.0),
            (0.05, 0.10),
            (0.10, 0.20, 0.40),
            (6, 12),
            (24, 48),
            ("compression_close", "nested_breakout", "pullback_rejection"),
            (False, True),
            (False, True),
        )
    ]
    if exhaustive:
        return full
    selected = [
        SixMaMtfParameters(),
        SixMaMtfParameters(
            compression_4h_atr=2.0,
            compression_15m_atr=2.0,
            breakout_4h_atr=0.05,
            breakout_15m_atr=0.05,
            zone_max_age_4h=12,
            setup_wait_15m=48,
            require_full_4h_trend=False,
        ),
        SixMaMtfParameters(
            compression_4h_atr=2.0,
            compression_15m_atr=2.0,
            breakout_4h_atr=0.05,
            breakout_15m_atr=0.05,
            zone_max_age_4h=12,
            setup_wait_15m=48,
            entry_trigger="pullback_rejection",
            require_full_4h_trend=False,
        ),
        SixMaMtfParameters(
            compression_4h_atr=2.0,
            compression_15m_atr=2.0,
            breakout_4h_atr=0.05,
            breakout_15m_atr=0.05,
            zone_max_age_4h=12,
            setup_wait_15m=48,
            entry_trigger="compression_close",
            require_full_4h_trend=False,
        ),
    ]
    for value in (0.8, 1.2, 1.6, 2.0):
        selected.append(SixMaMtfParameters(compression_4h_atr=value))
    for value in (0.05, 0.10):
        selected.append(SixMaMtfParameters(breakout_4h_atr=value))
    for value in (0.8, 1.4, 2.0):
        selected.append(SixMaMtfParameters(compression_15m_atr=value))
    for value in (0.05, 0.10):
        selected.append(SixMaMtfParameters(breakout_15m_atr=value))
    for value in (0.10, 0.20, 0.40):
        selected.append(SixMaMtfParameters(stop_buffer_atr=value))
    for value in (6, 12):
        selected.append(SixMaMtfParameters(zone_max_age_4h=value))
    for value in (24, 48):
        selected.append(SixMaMtfParameters(setup_wait_15m=value))
    selected.append(SixMaMtfParameters(entry_trigger="pullback_rejection"))
    selected.append(SixMaMtfParameters(entry_trigger="compression_close"))
    selected.append(SixMaMtfParameters(require_full_4h_trend=False))
    selected.append(SixMaMtfParameters(strict_market_consensus=True))
    unique = list(dict.fromkeys(selected))
    for index in range(96):
        candidate = full[round(index * (len(full) - 1) / 95)]
        if candidate not in unique:
            unique.append(candidate)
        if len(unique) >= 32:
            break
    return unique


def parameter_dict(
    params: V2Parameters | DualMaParameters | SixMaMtfParameters,
) -> dict[str, object]:
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
