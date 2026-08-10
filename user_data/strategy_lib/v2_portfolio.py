"""Portfolio risk gates and deterministic V2 exit state transitions."""

from __future__ import annotations

from dataclasses import dataclass


MAX_PORTFOLIO_RISK = 0.02
NORMAL_TRADE_RISK = 0.0075
REDUCED_TRADE_RISK = 0.005


def risk_budget(
    open_risks: list[float],
    open_sides: list[str],
    new_side: str,
    equity_drawdown: float,
    *,
    daily_loss_r: float = 0.0,
    normal_trade_risk: float = NORMAL_TRADE_RISK,
    reduced_trade_risk: float = REDUCED_TRADE_RISK,
    max_portfolio_risk: float = MAX_PORTFOLIO_RISK,
) -> float:
    if len(open_risks) != len(open_sides):
        raise ValueError("open_risks and open_sides must have equal length")
    if new_side not in {"long", "short"}:
        raise ValueError("new_side must be long or short")
    if any(risk < 0 for risk in open_risks):
        raise ValueError("open risk cannot be negative")
    if len(open_risks) >= 3 or open_sides.count(new_side) >= 2:
        return 0.0
    if equity_drawdown >= 0.30 or daily_loss_r >= 4.0:
        return 0.0
    per_trade = reduced_trade_risk if equity_drawdown >= 0.20 else normal_trade_risk
    remaining = max(0.0, max_portfolio_risk - sum(open_risks))
    return min(per_trade, remaining)


@dataclass(frozen=True)
class TradeState:
    age_hours: float
    r_multiple: float
    partial_taken: bool
    rate: float
    entry: float
    ema20: float
    fees_ratio: float
    is_short: bool = False
    current_stop: float | None = None


@dataclass(frozen=True)
class ExitAction:
    reduce_fraction: float = 0.0
    new_stop: float | None = None
    full_exit_reason: str | None = None


def _break_even(state: TradeState) -> float:
    multiplier = 1 - state.fees_ratio if state.is_short else 1 + state.fees_ratio
    return state.entry * multiplier


def _tightened_stop(state: TradeState, proposed: float) -> float:
    if state.current_stop is None:
        return proposed
    return min(state.current_stop, proposed) if state.is_short else max(state.current_stop, proposed)


def exit_decision(state: TradeState) -> ExitAction:
    if state.r_multiple >= 5.0:
        return ExitAction(full_exit_reason="hard_5r")
    if state.age_hours >= 6 and state.r_multiple < 0.5:
        return ExitAction(full_exit_reason="no_progress_6h")
    if state.partial_taken and state.age_hours >= 24:
        return ExitAction(full_exit_reason="runner_24h")
    if not state.partial_taken and state.age_hours >= 12:
        return ExitAction(full_exit_reason="ordinary_12h")
    break_even = _break_even(state)
    if not state.partial_taken and state.r_multiple >= 2.0:
        return ExitAction(reduce_fraction=0.40, new_stop=_tightened_stop(state, break_even))
    if state.partial_taken:
        proposed = min(break_even, state.ema20) if state.is_short else max(break_even, state.ema20)
        return ExitAction(new_stop=_tightened_stop(state, proposed))
    return ExitAction()
