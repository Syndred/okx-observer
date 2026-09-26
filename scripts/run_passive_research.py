#!/usr/bin/env python3
"""Fixed passive-entry scenario screen; independent labels are not portfolio proof."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from itertools import product
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import pandas as pd
from scripts.run_jev_profit_research import DEV_SYMBOLS,START,SPLIT_A,SPLIT_B,END,load_dataset,stats,fingerprint,write_json
from scripts.run_cost_aware_research import train_rank
from user_data.strategy_lib.cost_aware_signal_engine import CostAwareParameters,scan_cost_aware
from user_data.strategy_lib.passive_scalp_paths import PassiveOrder,evaluate_passive


def make_orders(signals,offset,target):
    orders=[]
    for pair,frame in signals.items():
        for row in frame.loc[frame.side.ne('')].itertuples():
            sign=1 if row.side=='long' else -1
            limit=float(row.close)-sign*offset*float(row.atr14)
            stop=float(row.initial_stop_price)
            if limit<=0 or sign*(limit-stop)<=0 or abs(limit-stop)*target/limit<.004:continue
            orders.append(PassiveOrder(pd.Timestamp(row.decision_at),pair,row.side,limit,stop))
    return sorted(orders,key=lambda e:(e.date,e.pair,e.side))


def window(orders,begin,end,hold):
    return [e for e in orders if begin<=e.date and e.date+pd.Timedelta(minutes=hold)<=end]


def measure(frames,funding,orders,target,hold,stress=False,strict_fill=True):
    factor=2 if stress else 1
    rows=evaluate_passive(frames,orders,target,hold,entry_fee=.0002*factor,exit_fee=.0005*factor,
          exit_slippage=.0005*factor,penetration=.0001*(factor if strict_fill else 1),funding_frames=funding,missing_funding=.0001*factor)
    result={**stats(rows),'orders':len(orders),'fill_rate':len(rows)/len(orders) if orders else None,
            'skipped_counts':rows.attrs.get('skipped_counts',{})}
    return result,rows


def passes(regular,stress,minimum):
    return (regular['n']>=minimum and regular['pf'] is not None and regular['pf']>=1.2
            and regular['mean_net_return']>0 and stress['n']>=minimum and stress['pf'] is not None
            and stress['pf']>=1 and stress['mean_net_return']>=0)


def run(args):
    out=args.output_dir
    if out.exists() and any(out.iterdir()):raise ValueError('output directory must be empty')
    out.mkdir(parents=True,exist_ok=True)
    paths=['scripts/run_passive_research.py','user_data/strategy_lib/passive_scalp_paths.py',
           'user_data/strategy_lib/cost_aware_signal_engine.py','user_data/strategy_lib/scalp_signal_engine.py',
           'user_data/strategy_lib/profit_signal_engine.py']
    manifest={'status':'development','grid_trials':64,'heldout_prices_opened':False,'live_claim_allowed':False,
              'portfolio_verified':False,'execution_kind':'OHLC scenario, not observed queue fills',
              'protocol_sha256':fingerprint(ROOT/'docs/research/JEV_PASSIVE_PROTOCOL.md'),
              'code_sha256':{p:fingerprint(ROOT/p) for p in paths},'created_at':pd.Timestamp.now(tz='UTC').isoformat()}
    write_json(out/'manifest.json',manifest)
    frames,funding,hashes=load_dataset(args.data_dir,DEV_SYMBOLS,START)
    manifest['development_data_sha256']=hashes;write_json(out/'manifest.json',manifest)
    configs={};all_orders={};training=[]
    for family,stop,atr in product(['retest','breakout'],[1.5,3.],[0.,.004]):
        params=CostAwareParameters(family,stop,atr,0.)
        signals={pair:scan_cost_aware(frame,params) for pair,frame in frames.items()}
        for offset,target,hold in product([.25,.75],[1.,2.],[30,60]):
            cid=len(configs);configs[cid]=(params,offset,target,hold)
            orders=make_orders(signals,offset,target);all_orders[cid]=orders
            subset=window(orders,START,SPLIT_A,hold)
            normal,_=measure(frames,funding,subset,target,hold);stress,_=measure(frames,funding,subset,target,hold,True)
            cost_only,_=measure(frames,funding,subset,target,hold,True,False)
            training.append({'candidate':cid,**asdict(params),'offset_atr':offset,'target_r':target,
                             'hold_minutes':hold,**normal,'stress_pf':stress['pf'],'stress_n':stress['n'],
                             'stress_mean_net_return':stress['mean_net_return'],
                             'cost_only_stress_pf':cost_only['pf'],'cost_only_stress_n':cost_only['n'],
                             'cost_only_stress_mean_net_return':cost_only['mean_net_return'],
                             'training_passed':passes(normal,stress,80) and passes(normal,cost_only,80)})
        pd.DataFrame(training).to_csv(out/'training-grid.csv',index=False)
        print(f'training {len(training)}/64',flush=True)
    records=[]
    for row in sorted(training,key=train_rank,reverse=True)[:6]:
        cid=row['candidate'];params,offset,target,hold=configs[cid]
        subset=window(all_orders[cid],SPLIT_A,SPLIT_B,hold)
        normal,rows=measure(frames,funding,subset,target,hold);stress,srows=measure(frames,funding,subset,target,hold,True)
        cost_only,crows=measure(frames,funding,subset,target,hold,True,False)
        rows.to_csv(out/f'candidate-{cid}-a-labels.csv',index=False)
        srows.to_csv(out/f'candidate-{cid}-a-stress-labels.csv',index=False)
        crows.to_csv(out/f'candidate-{cid}-a-cost-only-stress-labels.csv',index=False)
        record={'candidate':cid,'training':row,'regular':normal,'stress':stress,'cost_only_stress':cost_only,
                'passed':bool(row['training_passed'] and passes(normal,stress,30) and passes(normal,cost_only,30))}
        records.append(record);write_json(out/'development-a.json',{'candidates':records})
        print('A',cid,record['passed'],normal['n'],normal['pf'],flush=True)
    qualifying=[r for r in records if r['passed']]
    if not qualifying:
        manifest.update(status='no_profitable_development_candidate',reason='Training/A independent labels failed; B performance and reserved unopened')
    else:
        chosen=max(qualifying,key=lambda r:(r['stress']['pf'],r['regular']['pf']))
        cid=chosen['candidate'];params,offset,target,hold=configs[cid]
        frozen={'candidate':cid,'parameters':asdict(params),'offset_atr':offset,'target_r':target,'hold_minutes':hold,
                'a':chosen,'frozen_at':pd.Timestamp.now(tz='UTC').isoformat()}
        write_json(out/'frozen-candidate.json',frozen)
        subset=window(all_orders[cid],SPLIT_B,END,hold)
        normal,rows=measure(frames,funding,subset,target,hold);stress,srows=measure(frames,funding,subset,target,hold,True)
        cost_only,crows=measure(frames,funding,subset,target,hold,True,False)
        rows.to_csv(out/'selected-b-labels.csv',index=False);srows.to_csv(out/'selected-b-stress-labels.csv',index=False)
        crows.to_csv(out/'selected-b-cost-only-stress-labels.csv',index=False)
        passed=passes(normal,stress,30) and passes(normal,cost_only,30) and normal['n']+chosen['regular']['n']>=100
        frozen['b']={'regular':normal,'stress':stress,'cost_only_stress':cost_only,'passed':bool(passed)};write_json(out/'frozen-candidate.json',frozen)
        manifest['status']='labels_passed_requires_portfolio_and_execution_validation' if passed else 'development_b_failed'
    write_json(out/'manifest.json',manifest);print(manifest['status'],flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('user_data/data/okx_scalp'))
    parser.add_argument('--output-dir',type=Path,default=Path('user_data/backtest_results/passive-20260927'))
    args=parser.parse_args()
    try:run(args)
    except Exception as error:
        import json
        p=args.output_dir/'manifest.json'
        if p.exists():
            state=json.loads(p.read_text())
            if state['status']=='development':state.update(status='failed',error_type=type(error).__name__);write_json(p,state)
        raise
if __name__=='__main__':main()
