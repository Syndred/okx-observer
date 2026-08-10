"""Event-driven, conservative portfolio simulator for V2 research."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import pandas as pd

from user_data.strategy_lib.v2_portfolio import TradeState, exit_decision, risk_budget


@dataclass(frozen=True)
class BacktestOptions:
    initial_equity: float = 100.0
    leverage: float = 3.0
    exit_mode: str = "hybrid"
    time_mode: str = "default"
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0005
    min_notional: float = 5.0
    compounding_cap_multiple: float | None = None
    normal_trade_risk: float = 0.0075
    reduced_trade_risk: float = 0.005
    max_portfolio_risk: float = 0.02


@dataclass(frozen=True)
class EntryEvent:
    date: pd.Timestamp
    pair: str
    side: str
    stop_price: float
    rank: int
    category: str = "1"


@dataclass(frozen=True)
class TradeResult:
    pair: str
    side: str
    open_date: pd.Timestamp
    close_date: pd.Timestamp
    entry_price: float
    exit_price: float
    net_pnl: float
    initial_risk: float
    r_multiple: float
    exit_reason: str
    partial_taken: bool
    fees: float
    funding: float
    category: str


@dataclass
class BacktestResult:
    trades: list[TradeResult]
    equity_curve: pd.DataFrame
    initial_equity: float
    final_equity: float
    max_concurrent_positions: int
    skipped_entries: int


@dataclass
class _Position:
    pair: str
    side: str
    category: str
    open_date: pd.Timestamp
    entry: float
    stop: float
    initial_stop: float
    amount: float
    original_amount: float
    initial_risk_usdt: float
    risk_pct: float
    rank: int
    fees: float
    funding: float
    realized_pnl: float
    partial_taken: bool = False


def _normalize_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    output = {}
    for pair, frame in frames.items():
        source = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
        source["date"] = pd.to_datetime(source["date"], utc=True)
        if "ema20" not in source:
            source["ema20"] = source["close"].ewm(span=20, adjust=False).mean()
        output[pair] = source.set_index("date", drop=False)
    return output


def _exit_fill(price: float, side: str, slippage: float) -> float:
    return price * (1 - slippage if side == "long" else 1 + slippage)


def _gross_pnl(position: _Position, amount: float, exit_price: float) -> float:
    move = exit_price - position.entry
    return amount * (move if position.side == "long" else -move)


def _mark_pnl(position: _Position, rate: float) -> float:
    return _gross_pnl(position, position.amount, rate)


def _close_amount(
    position: _Position,
    amount: float,
    raw_price: float,
    equity: float,
    options: BacktestOptions,
) -> tuple[float, float, float]:
    fill = _exit_fill(raw_price, position.side, options.slippage_rate)
    gross = _gross_pnl(position, amount, fill)
    fee = abs(amount * fill) * options.fee_rate
    net = gross - fee
    position.amount -= amount
    position.realized_pnl += net
    position.fees += fee
    return equity + net, fill, net


def _finalize(
    position: _Position,
    date: pd.Timestamp,
    exit_price: float,
    reason: str,
) -> TradeResult:
    pnl = position.realized_pnl
    return TradeResult(
        pair=position.pair,
        side=position.side,
        open_date=position.open_date,
        close_date=date,
        entry_price=position.entry,
        exit_price=exit_price,
        net_pnl=pnl,
        initial_risk=position.initial_risk_usdt,
        r_multiple=pnl / position.initial_risk_usdt if position.initial_risk_usdt else 0.0,
        exit_reason=reason,
        partial_taken=position.partial_taken,
        fees=position.fees,
        funding=position.funding,
        category=position.category,
    )


def simulate_portfolio(
    frames: dict[str, pd.DataFrame],
    entry_events: list[EntryEvent],
    options: BacktestOptions | None = None,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    funding_frames: dict[str, pd.DataFrame] | None = None,
) -> BacktestResult:
    active = options or BacktestOptions()
    if active.exit_mode not in {"hybrid", "fixed3", "fixed5"}:
        raise ValueError("unsupported exit_mode")
    if active.time_mode not in {"default", "none", "24h"}:
        raise ValueError("unsupported time_mode")
    data = _normalize_frames(frames)
    funding_data: dict[str, pd.DataFrame] = {}
    for pair, frame in (funding_frames or {}).items():
        source = frame.copy()
        source["date"] = pd.to_datetime(source["date"], utc=True)
        funding_data[pair] = source.sort_values("date").drop_duplicates(
            "date", keep="last"
        ).set_index("date", drop=False)
    events_by_date: dict[pd.Timestamp, list[EntryEvent]] = {}
    for event in entry_events:
        timestamp = pd.Timestamp(event.date)
        timestamp = timestamp.tz_localize("UTC") if timestamp.tz is None else timestamp.tz_convert("UTC")
        if (start is not None and timestamp < start) or (end is not None and timestamp >= end):
            continue
        events_by_date.setdefault(timestamp, []).append(event)
    timeline = sorted(
        {
            timestamp
            for frame in data.values()
            for timestamp in frame.index
            if (start is None or timestamp >= start) and (end is None or timestamp < end)
        }
    )
    equity = active.initial_equity
    equity_peak = equity
    positions: dict[str, _Position] = {}
    trades: list[TradeResult] = []
    curve: list[dict[str, object]] = []
    skipped = 0
    max_concurrent = 0
    daily_loss_r = 0.0
    current_day = None

    for timestamp in timeline:
        day = timestamp.date()
        if day != current_day:
            daily_loss_r = 0.0
            current_day = day
        for pair, position in positions.items():
            funding = funding_data.get(pair)
            if funding is None or timestamp not in funding.index or timestamp not in data[pair].index:
                continue
            rate = float(funding.loc[timestamp]["rate"])
            mark = float(data[pair].loc[timestamp]["open"])
            payment = position.amount * mark * rate * (
                1 if position.side == "long" else -1
            )
            equity -= payment
            position.realized_pnl -= payment
            position.funding += payment
        for event in sorted(events_by_date.get(timestamp, []), key=lambda item: (item.rank, item.pair)):
            if event.pair in positions or event.pair not in data or timestamp not in data[event.pair].index:
                skipped += 1
                continue
            row = data[event.pair].loc[timestamp]
            raw_entry = float(row["open"])
            entry = raw_entry * (
                1 + active.slippage_rate if event.side == "long" else 1 - active.slippage_rate
            )
            stop_distance = abs(entry - event.stop_price)
            correctly_sided = event.stop_price < entry if event.side == "long" else event.stop_price > entry
            liquidation_buffer = 0.85 / active.leverage
            if (
                not correctly_sided
                or stop_distance <= 0
                or stop_distance / entry >= liquidation_buffer
            ):
                skipped += 1
                continue
            open_risks = [position.risk_pct for position in positions.values()]
            open_sides = [position.side for position in positions.values()]
            drawdown = 0.0 if equity_peak <= 0 else max(0.0, 1 - equity / equity_peak)
            budget = risk_budget(
                open_risks,
                open_sides,
                event.side,
                drawdown,
                daily_loss_r=daily_loss_r,
                normal_trade_risk=active.normal_trade_risk,
                reduced_trade_risk=active.reduced_trade_risk,
                max_portfolio_risk=active.max_portfolio_risk,
            )
            if budget <= 0:
                skipped += 1
                continue
            sizing_equity = equity
            if active.compounding_cap_multiple is not None:
                sizing_equity = min(
                    sizing_equity,
                    active.initial_equity * active.compounding_cap_multiple,
                )
            intended_risk = sizing_equity * budget
            notional = intended_risk / (stop_distance / entry)
            used_collateral = sum(
                position.amount * position.entry / active.leverage
                for position in positions.values()
            )
            available_collateral = max(0.0, equity - used_collateral)
            notional = min(notional, available_collateral * active.leverage)
            if notional < active.min_notional:
                skipped += 1
                continue
            amount = notional / entry
            actual_risk = amount * stop_distance
            entry_fee = notional * active.fee_rate
            equity -= entry_fee
            positions[event.pair] = _Position(
                pair=event.pair,
                side=event.side,
                category=event.category,
                open_date=timestamp,
                entry=entry,
                stop=event.stop_price,
                initial_stop=event.stop_price,
                amount=amount,
                original_amount=amount,
                initial_risk_usdt=actual_risk,
                risk_pct=actual_risk / max(equity, 1e-12),
                rank=event.rank,
                fees=entry_fee,
                funding=0.0,
                realized_pnl=-entry_fee,
            )
            max_concurrent = max(max_concurrent, len(positions))

        for pair in list(positions):
            position = positions[pair]
            frame = data.get(pair)
            if frame is None or timestamp not in frame.index:
                continue
            row = frame.loc[timestamp]
            candle_open = float(row["open"])
            high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
            stop_hit = low <= position.stop if position.side == "long" else high >= position.stop
            if stop_hit:
                reason = "trailing_stop" if position.partial_taken else "initial_stop"
                stop_fill = (
                    min(position.stop, candle_open)
                    if position.side == "long"
                    else max(position.stop, candle_open)
                )
                equity, fill, _ = _close_amount(
                    position, position.amount, stop_fill, equity, active
                )
                result = _finalize(position, timestamp, fill, reason)
                trades.append(result)
                if result.r_multiple < 0:
                    daily_loss_r += abs(result.r_multiple)
                del positions[pair]
                continue

            risk_price = abs(position.entry - position.initial_stop)
            hard_r = 3.0 if active.exit_mode == "fixed3" else 5.0
            target = position.entry + (
                risk_price * hard_r * (1 if position.side == "long" else -1)
            )
            target_hit = high >= target if position.side == "long" else low <= target
            if target_hit:
                equity, fill, _ = _close_amount(position, position.amount, target, equity, active)
                result = _finalize(position, timestamp, fill, f"fixed_{int(hard_r)}r")
                trades.append(result)
                del positions[pair]
                continue

            if active.exit_mode == "hybrid" and not position.partial_taken:
                partial_target = position.entry + (
                    risk_price * 2 * (1 if position.side == "long" else -1)
                )
                partial_hit = high >= partial_target if position.side == "long" else low <= partial_target
                if partial_hit:
                    amount = position.original_amount * 0.40
                    equity, _, _ = _close_amount(position, amount, partial_target, equity, active)
                    position.partial_taken = True
                    fee_buffer = 2 * active.fee_rate + 2 * active.slippage_rate
                    position.stop = position.entry * (
                        1 - fee_buffer if position.side == "short" else 1 + fee_buffer
                    )

            age_hours = (timestamp - position.open_date).total_seconds() / 3600
            current_r = (
                (position.entry - close if position.side == "short" else close - position.entry)
                / risk_price
            )
            full_exit_reason = None
            if active.time_mode == "default":
                full_exit_reason = exit_decision(
                    TradeState(
                        age_hours,
                        current_r,
                        position.partial_taken,
                        close,
                        position.entry,
                        float(row["ema20"]),
                        2 * active.fee_rate + 2 * active.slippage_rate,
                        is_short=position.side == "short",
                        current_stop=position.stop,
                    )
                ).full_exit_reason
            elif active.time_mode == "24h" and age_hours >= 24:
                full_exit_reason = "all_24h"
            if full_exit_reason:
                equity, fill, _ = _close_amount(position, position.amount, close, equity, active)
                result = _finalize(position, timestamp, fill, full_exit_reason)
                trades.append(result)
                if result.r_multiple < 0:
                    daily_loss_r += abs(result.r_multiple)
                del positions[pair]
                continue
            if active.exit_mode == "hybrid" and position.partial_taken:
                ema = float(row["ema20"])
                if position.side == "long":
                    position.stop = max(position.stop, ema)
                else:
                    position.stop = min(position.stop, ema)

        marked_equity = equity
        for pair, position in positions.items():
            if timestamp in data[pair].index:
                marked_equity += _mark_pnl(position, float(data[pair].loc[timestamp, "close"]))
        equity_peak = max(equity_peak, marked_equity)
        curve.append(
            {
                "date": timestamp,
                "equity": marked_equity,
                "realized_equity": equity,
                "open_positions": len(positions),
            }
        )

    if timeline:
        final_time = timeline[-1]
        for pair in list(positions):
            position = positions[pair]
            available = data[pair].loc[data[pair].index <= final_time]
            raw_exit = float(available.iloc[-1]["close"])
            equity, fill, _ = _close_amount(position, position.amount, raw_exit, equity, active)
            trades.append(_finalize(position, final_time, fill, "end_of_data"))
            del positions[pair]
        if curve:
            curve[-1]["equity"] = equity
            curve[-1]["realized_equity"] = equity
            curve[-1]["open_positions"] = 0
    return BacktestResult(
        trades=trades,
        equity_curve=pd.DataFrame(curve),
        initial_equity=active.initial_equity,
        final_equity=equity,
        max_concurrent_positions=max_concurrent,
        skipped_entries=skipped,
    )


def summarize_backtest(result: BacktestResult) -> dict[str, float | int]:
    pnls = [trade.net_pnl for trade in result.trades]
    holding_hours = [
        (pd.Timestamp(trade.close_date) - pd.Timestamp(trade.open_date)).total_seconds() / 3600
        for trade in result.trades
    ]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else (math.inf if wins else 0.0)
    consecutive = maximum = 0
    for pnl in pnls:
        consecutive = consecutive + 1 if pnl < 0 else 0
        maximum = max(maximum, consecutive)
    curve = result.equity_curve
    if curve.empty:
        max_drawdown = 0.0
        minimum = maximum_equity = result.initial_equity
    else:
        equities = curve["equity"].astype(float)
        peaks = equities.cummax()
        max_drawdown = float(((peaks - equities) / peaks.replace(0, math.nan)).max() or 0)
        minimum = float(equities.min())
        maximum_equity = float(equities.max())
    return {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "average_win": sum(wins) / len(wins) if wins else 0.0,
        "average_loss": sum(losses) / len(losses) if losses else 0.0,
        "payoff_ratio": (
            (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
            if wins and losses
            else 0.0
        ),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "pf": profit_factor,
        "drawdown": max_drawdown,
        "max_consecutive_losses": maximum,
        "average_holding_hours": (
            sum(holding_hours) / len(holding_hours) if holding_hours else 0.0
        ),
        "median_holding_hours": (
            float(pd.Series(holding_hours).median()) if holding_hours else 0.0
        ),
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "return_pct": (result.final_equity / result.initial_equity - 1) * 100,
        "min_equity": minimum,
        "max_equity": maximum_equity,
        "max_concurrent_positions": result.max_concurrent_positions,
        "skipped_entries": result.skipped_entries,
        "fees": sum(trade.fees for trade in result.trades),
        "funding": sum(trade.funding for trade in result.trades),
    }
