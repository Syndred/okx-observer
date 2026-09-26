from argparse import Namespace
from pathlib import Path
import json
import pandas as pd
import pytest
from scripts import run_passive_research as study


def test_order_price_uses_closed_signal_only():
    frame=pd.DataFrame([{'side':'long','close':100.,'atr14':2.,'initial_stop_price':94.,'decision_at':pd.Timestamp('2026-08-01',tz='UTC')}])
    order=study.make_orders({'X':frame},.75,1)[0]
    assert order.limit_price==98.5 and order.stop_price==94
    assert order.date==frame.iloc[0].decision_at


def test_wrong_side_stop_and_insufficient_space_rejected():
    frame=pd.DataFrame([{'side':'long','close':100.,'atr14':2.,'initial_stop_price':99.,'decision_at':pd.Timestamp('2026-08-01',tz='UTC')}])
    assert not study.make_orders({'X':frame},.75,1)
    assert not study.make_orders({'X':frame},.4,1)


def test_zero_or_low_samples_cannot_pass():
    regular={'n':3,'pf':20.,'mean_net_return':.1}
    assert not study.passes(regular,regular,30)
    adequate={'n':100,'pf':1.3,'mean_net_return':.001}
    assert not study.passes(adequate,regular,30)


def test_failed_a_does_not_evaluate_b_or_open_reserved(tmp_path,monkeypatch):
    reads=[];periods=[]
    def data(directory,*args):reads.append(directory);return {},{},{}
    monkeypatch.setattr(study,'load_dataset',data)
    monkeypatch.setattr(study,'make_orders',lambda *a:[])
    original=study.window
    def window(orders,start,end,hold):periods.append((start,end));return original(orders,start,end,hold)
    monkeypatch.setattr(study,'window',window)
    def measure(*args,**kwargs):
        return {'n':0,'pf':None,'mean_net_return':None,'orders':0,'fill_rate':None,'skipped_counts':{}},pd.DataFrame()
    monkeypatch.setattr(study,'measure',measure)
    study.run(Namespace(output_dir=tmp_path/'results',data_dir=Path('/development')))
    assert reads==[Path('/development')]
    assert all(end<=study.SPLIT_B for _,end in periods)
    manifest=json.loads((tmp_path/'results/manifest.json').read_text())
    assert manifest['status']=='no_profitable_development_candidate'
    assert manifest['heldout_prices_opened'] is False and manifest['portfolio_verified'] is False
    assert len(pd.read_csv(tmp_path/'results/training-grid.csv'))==64


def test_cost_only_pressure_keeps_fill_while_strict_pressure_can_reject():
    date=pd.Timestamp('2026-08-01T01:00:00Z')
    frame=pd.DataFrame({'date':pd.date_range(date,periods=2,freq='5min'),
        'open':[101.,100.1],'high':[101.1,100.3],'low':[99.985,100.0],'close':[100.1,100.2]})
    order=study.PassiveOrder(date,'X','long',100.,95.)
    regular,_=study.measure({'X':frame},{},[order],1.,10)
    strict,_=study.measure({'X':frame},{},[order],1.,10,True)
    cost_only,_=study.measure({'X':frame},{},[order],1.,10,True,False)
    assert regular['n']==cost_only['n']==1 and strict['n']==0
    assert regular['mean_net_return']>0>cost_only['mean_net_return']
