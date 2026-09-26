"""Synthetic train-only passive Jev causality and selection checks."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from scripts import run_jev_passive_filter as research
from user_data.strategy_lib.passive_scalp_paths import PassiveOrder


def inputs():
    dates = pd.date_range('2026-08-01', periods=31, freq='5min', tz='UTC')
    signals = pd.DataFrame({
        'date': dates-pd.Timedelta(minutes=5), 'decision_at': dates,
        'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 20.,
        'fast': 100.1, 'slow': 99.9, 'atr14': 1., 'bias15': .2, 'bias60': .3,
        'compression': .2, 'instrument': 'SECRET-USDT-SWAP',
        'future_fill': True, 'net_return': 999.,
    })
    order = PassiveOrder(dates[25], 'SECRET-USDT-SWAP', 'long', 99.5, 98.)
    funding = pd.DataFrame({'date': [dates[0], dates[25], dates[26]], 'rate': [.0001, -.0002, .8]})
    return order, signals, funding


def test_state_uses_only_closed_rows_and_historical_funding():
    order, signals, funding = inputs()
    state = research.state_for_order(order, signals, funding)
    changed = signals.copy()
    cols = ['open', 'high', 'low', 'close', 'volume', 'fast', 'slow', 'atr14', 'bias15', 'bias60', 'compression']
    changed.loc[changed.decision_at > order.date, cols] = 999999.
    changed['future_fill'] = False
    changed['net_return'] = -999.
    rates = funding.copy()
    rates.loc[rates.date > order.date, 'rate'] = -999.
    assert research.state_for_order(order, changed, rates) == state
    assert len(state['bars_5m_oldest_first']) == 24
    assert state['last_known_funding_rate'] == -.0002
    assert state['bars_5m_oldest_first'][-1]['close'] == 0
    with pytest.raises(ValueError, match='missing causal decision'):
        research.state_for_order(order, signals.loc[signals.decision_at != order.date], funding)


def test_state_has_no_calendar_instrument_or_outcome_labels():
    order, signals, funding = inputs()
    original = research.state_for_order(order, signals, funding)
    shifted = signals.copy()
    shifted[['date', 'decision_at']] += pd.Timedelta(days=100)
    shifted['instrument'] = 'OTHER-USDT-SWAP'
    shifted_funding = funding.copy()
    shifted_funding['date'] += pd.Timedelta(days=100)
    shifted_order = replace(order, date=order.date+pd.Timedelta(days=100), pair='OTHER-USDT-SWAP')
    assert research.state_for_order(shifted_order, shifted, shifted_funding) == original
    encoded = json.dumps(original)
    for forbidden in ['SECRET', '2026-08-01', 'future_fill', 'net_return']:
        assert forbidden not in encoded


def test_state_explicitly_predicts_net_profit_conditional_on_unknown_fill():
    order, signals, funding = inputs()
    state = research.state_for_order(order, signals, funding)
    rules = state['execution_rules']
    assert state['hypothesis'] == 'passive-limit-net-profit-conditional-on-fill-v1'
    assert 'CONDITIONAL ON' in rules['outcome']
    assert 'Unfilled orders are excluded' in rules['outcome']
    assert 'Future fill status is unknown' in rules['outcome']
    assert rules['limit_price_divided_by_last_closed_price'] == .995
    assert rules['fixed_stop_price_divided_by_last_closed_price'] == .98
    assert rules['entry_maker_fee'] == .0002
    assert rules['exit_taker_fee'] == rules['exit_adverse_slippage'] == .0005
    assert rules['entry_slippage'] == 0
    assert rules['target_r'] == 2
    assert rules['maximum_minutes_from_order_activation'] == 60


def metrics():
    return {
        'regular': {'n': 80, 'pf': 1.2, 'mean_net_return': .001, 'win_rate': .5},
        'cost_only_stress': {'n': 80, 'pf': 1., 'mean_net_return': 0., 'win_rate': .4},
        'strict_stress': {'n': 80, 'pf': 1., 'mean_net_return': 0., 'win_rate': .4},
    }


@pytest.mark.parametrize('name,update', [
    ('regular', {'n': 79}), ('regular', {'pf': 1.199}), ('regular', {'pf': None}),
    ('regular', {'mean_net_return': 0}), ('regular', {'win_rate': .4999}),
    ('cost_only_stress', {'n': 79}), ('cost_only_stress', {'pf': .999}),
    ('cost_only_stress', {'pf': None}), ('cost_only_stress', {'mean_net_return': -.0001}),
    ('strict_stress', {'n': 79}), ('strict_stress', {'pf': .999}),
    ('strict_stress', {'pf': None}), ('strict_stress', {'mean_net_return': -.0001}),
])
def test_qualifies_enforces_each_sample_profit_win_and_stress_gate(name, update):
    result = metrics()
    assert research.qualifies(result, 80)
    result[name].update(update)
    assert not research.qualifies(result, 80)


def test_qualifies_accepts_exact_boundaries_and_requested_minimum():
    result = metrics()
    assert research.qualifies(result, 80)
    assert not research.qualifies(result, 81)
    for row in result.values():
        row['n'] = 30
    assert research.qualifies(result, 30)
    assert not research.qualifies(result, 80)


def test_threshold_selection_has_no_fallback_and_uses_only_passed_records():
    failed = {'threshold': .15, 'passed': False, 'metrics': metrics()}
    failed['metrics']['regular']['pf'] = 99
    assert research.choose_threshold([]) is None
    assert research.choose_threshold([failed]) is None
    first = {'threshold': .25, 'passed': True, 'metrics': metrics()}
    second = deepcopy(first)
    second['threshold'] = .35
    second['metrics']['cost_only_stress']['pf'] = 1.2
    second['metrics']['strict_stress']['pf'] = 1.1
    assert research.choose_threshold([failed, first, second]) is second


@pytest.mark.parametrize("mode", ["base", "extended", "long"])
def test_failed_training_scores_all_orders_before_fill_filter_and_stops(monkeypatch, tmp_path, mode):
    train_start={"base":research.START,"extended":pd.Timestamp("2026-07-01",tz="UTC"),"long":pd.Timestamp("2026-03-01",tz="UTC")}[mode]
    split_a=pd.Timestamp("2026-06-01",tz="UTC") if mode=="long" else research.SPLIT_A
    split_b=pd.Timestamp("2026-07-15",tz="UTC") if mode=="long" else research.SPLIT_B
    pair = 'SYNTHETIC'
    orders = [PassiveOrder(train_start+pd.Timedelta(hours=i+1), pair, 'long', 99, 98) for i in range(3)]
    orders += [replace(orders[0], date=split_a+pd.Timedelta(hours=1)),
               replace(orders[0], date=split_b+pd.Timedelta(hours=1))]
    dataset = Mock(return_value=({pair: pd.DataFrame()}, {}, {'synthetic': 'hash'}))
    monkeypatch.setattr(research, 'load_dataset', dataset)
    monkeypatch.setattr(research, 'fingerprint', lambda _: 'synthetic-hash')
    monkeypatch.setattr(research, 'scan_cost_aware', lambda *_: pd.DataFrame())
    monkeypatch.setattr(research, 'make_orders', lambda *_: orders)
    monkeypatch.setattr(research, 'load_key_file', Mock())
    monkeypatch.setattr(research, 'JevClient', Mock())
    scored = []
    def mock_score(client, signals, funding, subset, cache, out, name):
        scored.append((name, list(subset)))
        return ({research.event_key(o): {'probability': .4} for o in subset},
                {'orders': len(subset), 'cached': 0, 'api_calls': len(subset)})
    monkeypatch.setattr(research, 'score', mock_score)
    def mock_measure(frames, funding, subset, *args):
        # Only the first order hypothetically fills; the others must still be scored.
        assert scored and scored[0][1] == orders[:3]
        result = metrics()['regular']
        result.update(n=int(orders[0] in subset), pf=.8, mean_net_return=-.001)
        return result, pd.DataFrame()
    monkeypatch.setattr(research, 'measure', mock_measure)
    args = SimpleNamespace(output_dir=tmp_path/'out', data_dir=tmp_path/'development', env_file=None, extended_training=mode=="extended", long_history=mode=="long")
    research.run(args)
    assert scored == [('training', orders[:3])]
    dataset.assert_called_once_with(args.data_dir, research.DEV_SYMBOLS, train_start)
    manifest = json.loads((args.output_dir/'manifest.json').read_text())
    assert manifest['status'] == 'no_qualified_training_threshold'
    assert manifest['training_start']==train_start.isoformat()
    assert manifest['development_a_start']==split_a.isoformat()
    assert manifest['development_b_start']==split_b.isoformat()
    assert manifest['heldout_prices_opened'] is False
    assert manifest['portfolio_verified'] is False
    assert manifest['live_claim_allowed'] is False
    assert not (args.output_dir/'frozen-candidate.json').exists()
    assert not (args.output_dir/'a-metrics.json').exists()
    assert not (args.output_dir/'b-metrics.json').exists()


def test_score_predicts_each_prespecified_order_without_access_to_fill_outcomes(monkeypatch, tmp_path):
    order, signals, funding = inputs()
    orders = [order, replace(order, side='short', limit_price=100.5, stop_price=102.)]
    seen = []
    def fake_predict(client, state, cache):
        seen.append(state)
        return {'probability': .4}, True
    monkeypatch.setattr(research, 'predict', fake_predict)
    answers, usage = research.score(object(), {order.pair: signals}, {order.pair: funding},
                                    orders, tmp_path/'cache', tmp_path, 'synthetic')
    assert len(seen) == 2
    assert set(answers) == {research.event_key(o) for o in orders}
    assert {state['side'] for state in seen} == {'long', 'short'}
    assert usage == {'orders': 2, 'cached': 2, 'api_calls': 0}
    assert json.loads((tmp_path/'synthetic-predictions.json').read_text()) == answers


def test_all_metrics_runs_both_distinct_stress_scenarios(monkeypatch):
    measure = Mock(return_value=({'n': 0}, pd.DataFrame()))
    monkeypatch.setattr(research, 'measure', measure)
    result = research.all_metrics({}, {}, [])
    assert set(result) == {'regular', 'cost_only_stress', 'strict_stress'}
    assert [call.args[-2:] for call in measure.call_args_list] == [(False, True), (True, False), (True, True)]


def test_history_modes_cannot_be_combined():
    with pytest.raises(ValueError,match="one history mode"):
        research.study_windows(SimpleNamespace(extended_training=True,long_history=True))
