from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from user_data.strategy_lib.confirmed_retest_signal_engine import (
    BASE_PARAMETERS, scan_confirmed_retest,
)
from user_data.strategy_lib.profit_signal_engine import profit_events

MODULE = 'user_data.strategy_lib.confirmed_retest_signal_engine.scan_profit'


def fixture(short=False):
    dates = pd.date_range('2026-01-01', periods=4, freq='5min', tz='UTC')
    out = pd.DataFrame(dict(date=dates, decision_at=dates + pd.Timedelta(minutes=5),
                           open=[100., 101., 102., 103.], high=[102., 104., 105., 106.],
                           low=[99., 100., 101., 102.], close=[101., 103., 104., 105.],
                           atr14=1., side=['long', '', '', ''],
                           initial_stop_price=[98., np.nan, np.nan, np.nan]))
    if short:
        for c in ['open', 'high', 'low', 'close', 'initial_stop_price']:
            out[c] = 200 - out[c]
        out[['high', 'low']] = out[['low', 'high']].to_numpy()
        out.loc[0, 'side'] = 'short'
    return out


def run(source, stop_atr=2., min_atr_pct=0.):
    with patch(MODULE, return_value=source) as scanner:
        result = scan_confirmed_retest(pd.DataFrame(), stop_atr, min_atr_pct)
        assert scanner.call_args.args[1] == BASE_PARAMETERS
    return result


@pytest.mark.parametrize('short', [False, True])
def test_confirmed_row_only_and_next_open_time(short):
    source = fixture(short)
    result = run(source)
    assert result.side.tolist() == ['', 'short' if short else 'long', '', '']
    assert result.initial_stop_price.iloc[0:1].isna().all()
    events = profit_events('TEST', result, 2.)
    assert len(events) == 1
    assert events[0].date == source.date.iloc[2]
    assert events[0].stop_price == source.initial_stop_price.iloc[0]
    # A next-open bar need not exist for a closed-bar decision to be emitted.
    assert profit_events('TEST', run(source.iloc[:2]), 2.) == events


@pytest.mark.parametrize('short', [False, True])
def test_stop_widens_but_never_tightens(short):
    source = fixture(short)
    narrow, wide = run(source, 1.), run(source, 8.)
    assert narrow.initial_stop_price.iloc[1] == source.initial_stop_price.iloc[0]
    expected = source.close.iloc[1] + (8. if short else -8.)
    assert wide.initial_stop_price.iloc[1] == expected


@pytest.mark.parametrize('short', [False, True])
@pytest.mark.parametrize('reason', ['equal_boundary', 'not_confirmed', 'stop_touch', 'stop_broken', 'gap'])
def test_rejects_failed_confirmation_without_later_retry(short, reason):
    source = fixture(short)
    if reason in {'equal_boundary', 'not_confirmed'}:
        boundary = source.low.iloc[0] if short else source.high.iloc[0]
        source.loc[1, 'close'] = boundary + ((.1 if short else -.1) if reason == 'not_confirmed' else 0.)
    elif reason in {'stop_touch', 'stop_broken'}:
        source.loc[1, 'high' if short else 'low'] = source.initial_stop_price.iloc[0] + (
            (.1 if short else -.1) if reason == 'stop_broken' else 0.)
    else:
        source = source.drop(index=1).reset_index(drop=True)
    assert run(source).side.eq('').all()


def test_atr_fraction_threshold_and_invalid_inputs():
    source = fixture()
    threshold = source.atr14.iloc[1] / source.close.iloc[1]
    assert run(source, min_atr_pct=threshold).side.iloc[1] == 'long'
    assert run(source, min_atr_pct=threshold + .0001).side.eq('').all()
    for stop, minimum in [(0, 0), (-1, 0), (np.nan, 0), (np.inf, 0), (1, -1), (1, np.nan), (1, np.inf)]:
        with pytest.raises(ValueError):
            scan_confirmed_retest(pd.DataFrame(), stop, minimum)
    for field, value in [('atr14', np.nan), ('atr14', 0.), ('low', 0.), ('close', np.inf)]:
        bad = source.copy()
        bad.loc[1, field] = value
        assert run(bad).side.eq('').all()


def test_real_scan_causality_and_no_next_open_access():
    count = 420
    c = 100 + np.arange(count) * .001
    source = pd.DataFrame(dict(date=pd.date_range('2026-01-01', periods=count, freq='5min', tz='UTC'),
                               open=c-.01, high=c+.1, low=c-.1, close=c, volume=10., quote_volume=1000.))
    boundary = source.high.iloc[264:270].max()
    source.loc[270, ['open', 'high', 'low', 'close']] = [boundary-.1, boundary+.65, boundary-.11, boundary+.6]
    source.loc[271, ['open', 'high', 'low', 'close']] = [boundary+.2, boundary+.3, boundary-.04, boundary+.1]
    source.loc[272, ['open', 'high', 'low', 'close']] = [boundary+.2, boundary+.6, boundary+.1, boundary+.5]
    result = scan_confirmed_retest(source, 2.)
    assert result.side.iloc[272] == 'long'
    future = source.copy()
    future.loc[273:, ['open', 'high', 'low', 'close', 'volume']] *= 2
    changed = scan_confirmed_retest(future, 2.)
    pd.testing.assert_frame_equal(result.iloc[:273], changed.iloc[:273])
    pd.testing.assert_frame_equal(result.iloc[:273], scan_confirmed_retest(source.iloc[:273], 2.))
    events = profit_events('TEST', result.iloc[:273], 2.)
    assert len(events) == 1 and events[0].date == source.date.iloc[273]
