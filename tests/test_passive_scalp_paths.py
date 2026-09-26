"""Hypothetical OHLC fills: execution, cost arithmetic and data boundary tests."""
import numpy as np
import pandas as pd
import pytest

from user_data.strategy_lib.passive_scalp_paths import PassiveOrder, evaluate_passive


def frame(rows, start="2026-01-01 07:55"):
    result = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    result["date"] = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    return result


def evaluate(data, side="long", stop=None, **kwargs):
    order = PassiveOrder(data.date.iloc[0], "X", side, 100, stop or (95 if side == "long" else 105))
    return evaluate_passive({"X": data}, [order], 1, len(data) * 5, **kwargs)


@pytest.mark.parametrize("side,opening", [("long", 100), ("long", 99), ("short", 100), ("short", 101)])
def test_marketable_post_only_cancels(side, opening):
    result = evaluate(frame([(opening, 102, 98, 100)]), side)
    assert result.empty
    assert result.attrs["skipped_counts"] == {"marketable_at_open": 1}


@pytest.mark.parametrize("side,prices", [("long", (101, 102, 100, 101)), ("short", (99, 100, 98, 99))])
def test_touch_without_penetration_unfilled(side, prices):
    result = evaluate(frame([prices]), side)
    assert result.empty
    assert result.attrs["skipped_counts"] == {"not_filled": 1}


@pytest.mark.parametrize("side,prices", [("long", (101, 102, 99.99, 101)), ("short", (99, 100.01, 98, 99))])
def test_penetration_equality_fills(side, prices):
    assert len(evaluate(frame([prices]), side)) == 1


@pytest.mark.parametrize("side,prices", [("long", (101, 106, 94, 100)), ("short", (99, 106, 94, 100))])
def test_entry_candle_stop_first(side, prices):
    row = evaluate(frame([prices]), side).iloc[0]
    assert row.exit_reason == "initial_stop"
    assert row.hold_minutes == 0
    assert row.funding_return == 0


@pytest.mark.parametrize("side,rows", [
    ("long", [(101, 106, 99, 101), (101, 102, 99, 100)]),
    ("short", [(99, 101, 94, 99), (99, 101, 98, 100)]),
])
def test_no_entry_candle_take_profit(side, rows):
    row = evaluate(frame(rows), side).iloc[0]
    assert row.exit_reason == "max_hold_10m"
    assert row.hold_minutes == 10


@pytest.mark.parametrize("side,rows,expected", [
    ("long", [(101, 102, 99, 101), (93, 106, 92, 101)], 93 * .9995),
    ("short", [(99, 101, 98, 99), (107, 108, 94, 100)], 107 * 1.0005),
])
def test_later_gap_stop_precedes_target(side, rows, expected):
    row = evaluate(frame(rows), side).iloc[0]
    assert row.exit_reason == "initial_stop"
    assert row.exit_price == pytest.approx(expected)


@pytest.mark.parametrize("side,rows,rate", [
    ("long", [(101, 102, 99, 101), (102, 106, 101, 105)], .001),
    ("short", [(99, 101, 98, 99), (98, 99, 94, 95)], .001),
    ("long", [(101, 102, 99, 101), (102, 106, 101, 105)], -.001),
    ("short", [(99, 101, 98, 99), (98, 99, 94, 95)], -.001),
])
def test_fee_and_actual_funding_hand_calculation(side, rows, rate):
    data = frame(rows)
    funding = pd.DataFrame({"date": data.date, "rate": [.9, rate]})
    row = evaluate(data, side, funding_frames={"X": funding}).iloc[0]
    sign = 1 if side == "long" else -1
    exit_price = (100 + sign * 5) * (1 - sign * .0005)
    fees = .0002 + .0005 * exit_price / 100
    actual = rows[1][0] / 100 * rate * sign
    net = sign * (exit_price - 100) / 100 - fees - actual
    assert row.exit_price == pytest.approx(exit_price)
    assert row.fees_return == pytest.approx(fees)
    assert row.funding_actual_return == pytest.approx(actual)
    assert row.funding_imputed_return == 0
    assert row.net_return == pytest.approx(net)
    assert row.initial_risk_return == pytest.approx(.0512)
    assert row.r_multiple == pytest.approx(net / .0512)


@pytest.mark.parametrize("side,rows", [
    ("long", [(101, 102, 99, 101), (102, 103, 99, 101)]),
    ("short", [(99, 101, 98, 99), (98, 101, 97, 99)]),
])
def test_missing_funding_adverse_both_sides(side, rows):
    row = evaluate(frame(rows), side).iloc[0]
    assert row.funding_imputed_return == pytest.approx(rows[1][0] / 100 * .0001)
    assert row.funding_actual_return == 0


def test_nonstandard_actual_settlement_and_no_false_imputation():
    data = frame([(101, 102, 99, 101), (102, 103, 99, 101)], start="2026-01-01 02:00")
    funding = pd.DataFrame({"date": [data.date.iloc[1]], "rate": [-.001]})
    row = evaluate(data, funding_frames={"X": funding}).iloc[0]
    assert row.funding_actual_return == pytest.approx(-.00102)
    assert row.funding_imputed_return == 0


def test_missing_bar_rejects_even_if_entry_stop_hit():
    data = frame([(101, 102, 94, 100)] * 4).drop(index=1)
    result = evaluate(data)
    assert result.empty
    assert result.attrs["skipped_counts"] == {"incomplete_5m_window": 1}


def test_short_window_rejects():
    data = frame([(101, 102, 99, 101)])
    order = PassiveOrder(data.date.iloc[0], "X", "long", 100, 95)
    result = evaluate_passive({"X": data}, [order], 1, 10)
    assert result.attrs["skipped_counts"] == {"incomplete_5m_window": 1}


def test_outside_window_future_does_not_change_label():
    data = frame([(101, 102, 99, 101)] * 4)
    order = PassiveOrder(data.date.iloc[0], "X", "long", 100, 95)
    before = evaluate_passive({"X": data}, [order], 1, 10)
    data.loc[2:, ["open", "high", "low", "close"]] = [500, 600, 1, 2]
    after = evaluate_passive({"X": data}, [order], 1, 10)
    pd.testing.assert_frame_equal(before, after)


@pytest.mark.parametrize("kwargs", [{"target_r": 0}, {"target_r": np.inf}, {"hold_minutes": 7},
    {"hold_minutes": True}, {"penetration": -.1}, {"exit_slippage": 1},
    {"entry_fee": np.nan}, {"missing_funding": -.1}])
def test_invalid_configuration(kwargs):
    config = dict(target_r=1, hold_minutes=5)
    config.update(kwargs)
    with pytest.raises(ValueError):
        evaluate_passive({}, [], **config)


@pytest.mark.parametrize("limit,stop,reason", [(np.nan, 95, "invalid_limit"), (100, 101, "invalid_stop"), (100, np.inf, "invalid_stop")])
def test_invalid_order_prices(limit, stop, reason):
    data = frame([(101, 102, 99, 101)])
    result = evaluate_passive({"X": data}, [PassiveOrder(data.date.iloc[0], "X", "long", limit, stop)], 1, 5)
    assert result.attrs["skipped_counts"] == {reason: 1}


def test_unfilled_order_expires_before_next_bar():
    data = frame([(101, 102, 100.1, 101), (101, 102, 99, 101)])
    result = evaluate(data)
    assert result.empty
    assert result.attrs["skipped_counts"] == {"not_filled": 1}


def test_timeout_uses_final_close_and_full_hold():
    data = frame([(101, 102, 99, 101), (101, 103, 99, 102)])
    row = evaluate(data).iloc[0]
    assert row.exit_price == pytest.approx(102 * .9995)
    assert row.close_date == data.date.iloc[0] + pd.Timedelta(minutes=10)
    assert row.exit_reason == "max_hold_10m"


def test_duplicate_inside_required_window_rejected():
    data = frame([(101, 102, 99, 101)] * 2)
    data.loc[1, "date"] = data.date.iloc[0]
    result = evaluate(data)
    assert result.attrs["skipped_counts"] == {"incomplete_5m_window": 1}


def test_malformed_ohlc_rejected():
    result = evaluate(frame([(101, 100, 99, 101)]))
    assert result.attrs["skipped_counts"] == {"invalid_ohlc": 1}
