import numpy as np
import pandas as pd
import pytest

from user_data.strategy_lib.scalp_paths import evaluate_events
from user_data.strategy_lib.v2_backtester import BacktestOptions, EntryEvent, simulate_portfolio


def frame(rows, start="2026-09-01 07:55"):
    result = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    result["date"] = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    return result


def event(data, side="long", stop=None):
    return EntryEvent(data.date.iloc[0], "BTC/USDT", side,
                      stop if stop is not None else (99 if side == "long" else 101), 1)


@pytest.mark.parametrize("side,rows,reason", [
    ("long", [(100, 100.3, 99.8, 100.1), (100.1, 102, 100, 101)], "fixed_0.7r"),
    ("short", [(100, 100.2, 99.7, 99.9), (99.9, 100, 98, 99)], "fixed_0.7r"),
    ("long", [(100, 102, 98, 100), (100, 101, 99, 100)], "initial_stop"),
    ("short", [(100, 102, 98, 100), (100, 101, 99, 100)], "initial_stop"),
    ("long", [(100, 100.3, 99.8, 100.1), (98, 99, 97, 98)], "initial_stop"),
    ("short", [(100, 100.2, 99.7, 99.9), (102, 103, 101, 102)], "initial_stop"),
    ("long", [(100, 100.3, 99.8, 100.1), (100.1, 100.3, 99.8, 100.2)], "max_hold_10m"),
    ("short", [(100, 100.2, 99.7, 99.9), (99.9, 100.2, 99.7, 99.8)], "max_hold_10m"),
])
def test_matches_single_trade_portfolio(side, rows, reason):
    data = frame(rows)
    entry = event(data, side)
    labels = evaluate_events({entry.pair: data}, [entry], .7, 10)
    label = labels.iloc[0]
    result = simulate_portfolio({entry.pair: data}, [entry], BacktestOptions(
        exit_mode="fixed3", time_mode="none", take_profit_r=.7,
        max_hold_minutes=10, candle_minutes=5,
    ))
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert label.exit_reason == trade.exit_reason == reason
    assert label.entry_price == pytest.approx(trade.entry_price)
    assert label.exit_price == pytest.approx(trade.exit_price)
    assert label.close_date == trade.close_date
    assert label.r_multiple == pytest.approx(trade.r_multiple)
    entry_notional = trade.initial_risk / label.initial_risk_return
    assert label.net_return == pytest.approx(trade.net_pnl / entry_notional)
    assert label.funding_return == pytest.approx(trade.funding / entry_notional)
    assert label.fees_return == pytest.approx(trade.fees / entry_notional)


def test_target_gap_gets_target_not_open_and_entry_funding_not_charged():
    data = frame([(100, 100.2, 99.8, 100), (102, 103, 101, 102)], start="2026-09-01 08:00")
    result = evaluate_events({"BTC/USDT": data}, [event(data)], .5, 10).iloc[0]
    assert result.exit_price == pytest.approx(result.target_price * .9995)
    assert result.funding_return == 0


def test_funding_is_charged_to_both_sides_at_boundary_before_exit():
    data = frame([(100, 100.1, 99.9, 100)] * 2)
    for side in ("long", "short"):
        result = evaluate_events({"BTC/USDT": data}, [event(data, side)], 3, 10).iloc[0]
        assert result.funding_return == pytest.approx(100 * .0001 / result.entry_price)
        assert result.close_date == data.date.iloc[0] + pd.Timedelta(minutes=10)
        assert result.hold_minutes == 10
        assert not result.won


def test_skip_reasons_include_missing_and_invalid_data_even_with_early_hit():
    data = frame([(100, 103, 99.8, 101)] * 3).drop(index=1)
    events = [event(data), event(data, stop=101), EntryEvent(data.date.iloc[0], "MISSING", "long", 99, 1)]
    output = evaluate_events({"BTC/USDT": data}, events, .5, 15)
    assert output.empty
    assert output.attrs["skipped_counts"] == {"incomplete_5m_window": 2, "missing_pair": 1}
    full = frame([(100, 100.2, 99.8, 100)] * 3)
    invalid = evaluate_events({"BTC/USDT": full}, [event(full, stop=101)], .5, 15)
    assert invalid.attrs["skipped_counts"] == {"invalid_stop": 1}
    invalid_date = EntryEvent(full.date.iloc[0] + pd.Timedelta(minutes=1), "BTC/USDT", "long", 99, 1)
    assert evaluate_events({"BTC/USDT": full}, [invalid_date], .5, 15).attrs["skipped_counts"] == {"missing_entry_bar": 1}


@pytest.mark.parametrize("target,hold", [(0, 10), (np.nan, 10), (.5, 0), (.5, 7)])
def test_reject_invalid_parameters(target, hold):
    with pytest.raises(ValueError):
        evaluate_events({}, [], target, hold)


def test_last_candle_uses_stop_before_timeout_and_no_next_bar():
    data = frame([(100, 100.2, 99.8, 100), (100, 100.2, 98, 99), (99, 110, 90, 100)])
    output = evaluate_events({"BTC/USDT": data}, [event(data)], 3, 10).iloc[0]
    assert output.exit_reason == "initial_stop"
    assert output.close_date == data.date.iloc[1]
    assert output.exit_price == pytest.approx(99 * .9995)


def test_costs_can_turn_target_hit_into_net_loss():
    data = frame([(100, 101, 99.99, 100)] * 2)
    output = evaluate_events({"BTC/USDT": data}, [event(data, stop=99.98)], .1, 10).iloc[0]
    assert output.exit_reason == "fixed_0.1r"
    assert not output.won
    assert output.net_return < 0
