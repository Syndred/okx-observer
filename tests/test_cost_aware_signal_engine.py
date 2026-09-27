import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

from user_data.strategy_lib.cost_aware_signal_engine import CostAwareParameters, scan_cost_aware
from user_data.strategy_lib.profit_signal_engine import ProfitParameters, scan_profit, profit_events
from user_data.strategy_lib.scalp_signal_engine import ScalpParameters, scan_scalp


def candles(short=False):
    c = 100 + np.arange(420) * .001
    f = pd.DataFrame(dict(date=pd.date_range('2026-01-01', periods=420, freq='5min', tz='UTC'),
                          open=c-.01, high=c+.1, low=c-.1, close=c,
                          volume=10., quote_volume=1000.))
    boundary = f.high.iloc[264:270].max()
    f.loc[270, ['open', 'high', 'low', 'close']] = [boundary-.1, boundary+.65, boundary-.11, boundary+.6]
    f.loc[271, ['open', 'high', 'low', 'close']] = [boundary+.01, boundary+.3, boundary-.04, boundary+.1]
    if short:
        for col in ['open', 'high', 'low', 'close']:
            f[col] = 200 - f[col]
        f[['high', 'low']] = f[['low', 'high']].to_numpy()
    return f


@pytest.mark.parametrize('family', ['retest', 'breakout'])
def test_future_prices_and_truncated_higher_bucket_cannot_change_history(family):
    frame = candles()
    params = CostAwareParameters(family=family, stop_atr=3)
    original = scan_cost_aware(frame, params)
    changed = frame.copy()
    changed.loc[272:, ['open', 'high', 'low', 'close', 'volume']] *= 10
    pd.testing.assert_frame_equal(original.iloc[:272], scan_cost_aware(changed, params).iloc[:272])
    pd.testing.assert_frame_equal(original.iloc[:272], scan_cost_aware(frame.iloc[:272], params))
    assert original.side.ne('').any()
    assert profit_events('TEST', original.iloc[:272], 3) == profit_events(
        'TEST', scan_cost_aware(changed, params).iloc[:272], 3)
    assert {'bias15', 'bias60', 'atr15', 'atr60', 'fast', 'slow', 'compression'} <= set(original)
    assert original.available60.iloc[271] == pd.Timestamp('2026-01-01 22:00', tz='UTC')


@pytest.mark.parametrize('family', ['retest', 'breakout'])
@pytest.mark.parametrize('short', [False, True])
@pytest.mark.parametrize('stop_atr', [.1, 4.])
def test_stops_only_widen_and_original_prices_are_preserved(family, short, stop_atr):
    frame = candles(short)
    base = (scan_profit(frame, ProfitParameters(fast=20, slow=60, stop_buffer_atr=.3))
            if family == 'retest' else scan_scalp(frame, ScalpParameters()))
    out = scan_cost_aware(frame, CostAwareParameters(family=family, stop_atr=stop_atr))
    mask = out.side.ne('')
    assert mask.any()
    assert out.loc[mask, 'side'].eq('short' if short else 'long').all()
    if short:
        assert out.loc[mask, 'initial_stop_price'].ge(base.loc[mask, 'initial_stop_price']).all()
        assert out.loc[mask, 'initial_stop_price'].ge(out.loc[mask, 'close'] + stop_atr*out.loc[mask, 'atr14']).all()
    else:
        assert out.loc[mask, 'initial_stop_price'].le(base.loc[mask, 'initial_stop_price']).all()
        assert out.loc[mask, 'initial_stop_price'].le(out.loc[mask, 'close'] - stop_atr*out.loc[mask, 'atr14']).all()
    pd.testing.assert_frame_equal(out[['open', 'high', 'low', 'close']], frame[['open', 'high', 'low', 'close']])


def test_volatility_and_directional_body_gates_include_equal_boundary():
    source = pd.DataFrame(dict(side=['long', 'short', 'long', 'short'],
                               close=[100.]*4, open=[99., 101., 101., 99.],
                               atr14=[2.]*4, initial_stop_price=[99., 101., 99., 101.]))
    with patch('user_data.strategy_lib.cost_aware_signal_engine.scan_profit', return_value=source.copy()):
        out = scan_cost_aware(pd.DataFrame(), CostAwareParameters(min_atr_pct=.02, min_body_atr=.5))
    assert out.side.tolist() == ['long', 'short', '', '']
    assert out.initial_stop_price.iloc[2:].isna().all()
    for p in [CostAwareParameters(min_atr_pct=.020001), CostAwareParameters(min_body_atr=.50001)]:
        with patch('user_data.strategy_lib.cost_aware_signal_engine.scan_profit', return_value=source.copy()):
            assert scan_cost_aware(pd.DataFrame(), p).side.eq('').all()


def test_trend_pullback_family_uses_existing_profit_engine_and_atr_stop_floor():
    source = pd.DataFrame(dict(date=[pd.Timestamp('2026-01-01',tz='UTC')],
                               open=[99.], high=[101.], low=[98.], close=[100.],
                               volume=[10.], quote_volume=[1000.], atr14=[2.],
                               initial_stop_price=[98.], side=['long']))
    with patch('user_data.strategy_lib.cost_aware_signal_engine.scan_profit', return_value=source.copy()) as scan:
        out = scan_cost_aware(pd.DataFrame(), CostAwareParameters(family='trend_pullback',stop_atr=3.))
    params=scan.call_args.args[1]
    assert params.family == 'trend_pullback'
    assert params.fast == 20 and params.slow == 60 and params.cooldown_bars == 12
    assert out.side.tolist() == ['long']
    assert out.initial_stop_price.iloc[0] == 94.


@pytest.mark.parametrize('kwargs', [dict(family='unknown'), dict(stop_atr=0), dict(stop_atr=-1),
                                    dict(stop_atr=np.inf), dict(stop_atr=np.nan), dict(stop_atr='x'),
                                    dict(min_atr_pct=-1), dict(min_atr_pct=np.nan),
                                    dict(min_body_atr=-1), dict(min_body_atr=np.inf)])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        scan_cost_aware(candles(), CostAwareParameters(**kwargs))
