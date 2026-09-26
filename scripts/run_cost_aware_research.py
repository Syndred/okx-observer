#!/usr/bin/env python3
"""Fixed cost-aware development experiment; no reserved prices or live orders."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from itertools import product
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import pandas as pd
from scripts.run_jev_profit_research import (DEV_SYMBOLS,START,SPLIT_A,SPLIT_B,END,
    load_dataset,period_events,event_set,label,stats,measure_portfolio,independent_pass,fingerprint,write_json)
from user_data.strategy_lib.cost_aware_signal_engine import CostAwareParameters,scan_cost_aware
from user_data.strategy_lib.scalp_paths import evaluate_events


def train_rank(row):
    return (row['n']>=80, row['pf'] if row['pf'] is not None else -1,
            row['mean_net_return'] if row['mean_net_return'] is not None else -1,row['n'])


def select_a(records):
    qualifying=[r for r in records if r['passed']]
    if not qualifying: return None
    return max(qualifying,key=lambda r:(r['metrics']['stress']['pf'],r['metrics']['regular']['pf']))


def run(args):
    out=args.output_dir
    if out.exists() and any(out.iterdir()): raise ValueError('output directory must be empty')
    out.mkdir(parents=True,exist_ok=True)
    sources=['scripts/run_cost_aware_research.py','user_data/strategy_lib/cost_aware_signal_engine.py',
             'user_data/strategy_lib/profit_signal_engine.py','user_data/strategy_lib/scalp_signal_engine.py',
             'user_data/strategy_lib/scalp_paths.py','user_data/strategy_lib/v2_backtester.py']
    manifest={'status':'development','grid_trials':96,'heldout_prices_opened':False,
              'live_claim_allowed':False,'created_at':pd.Timestamp.now(tz='UTC').isoformat(),
              'protocol_sha256':fingerprint(ROOT/'docs/research/JEV_COST_AWARE_PROTOCOL.md'),
              'code_sha256':{p:fingerprint(ROOT/p) for p in sources}}
    write_json(out/'manifest.json',manifest)
    frames,funding,hashes=load_dataset(args.data_dir,DEV_SYMBOLS,START)
    manifest['development_data_sha256']=hashes;write_json(out/'manifest.json',manifest)
    training=[];configs={};events_by_id={}
    for family,stop,atr,body in product(['retest','breakout'],[2.,3.],[.002,.004,.006],[0.,.5]):
        params=CostAwareParameters(family=family,stop_atr=stop,min_atr_pct=atr,min_body_atr=body)
        signals={pair:scan_cost_aware(frame,params) for pair,frame in frames.items()}
        for target,hold in product([1.,2.],[30,60]):
            cid=len(configs);configs[cid]=(params,target,hold)
            events=event_set(signals,target);events_by_id[cid]=events
            subset=period_events(events,START,SPLIT_A,hold)
            regular=stats(label(frames,funding,subset,target,hold))
            stress=stats(label(frames,funding,subset,target,hold,True))
            zero=stats(evaluate_events(frames,subset,target,hold,fee_rate=0,slippage_rate=0,
                       funding_rate_per_8h=0,funding_frames=funding))
            training.append({'candidate':cid,**asdict(params),'target_r':target,'hold_minutes':hold,
                             **regular,'stress_pf':stress['pf'],'zero_execution_cost_pf':zero['pf']})
        pd.DataFrame(training).to_csv(out/'training-grid.csv',index=False)
        print(f'training {len(training)}/96',flush=True)
    shortlist=sorted(training,key=train_rank,reverse=True)[:6]
    results=[]
    for row in shortlist:
        cid=row['candidate'];params,target,hold=configs[cid]
        metrics=measure_portfolio(out,f'candidate-{cid}-a',frames,funding,
            period_events(events_by_id[cid],SPLIT_A,SPLIT_B,hold),target,hold,SPLIT_A,SPLIT_B)
        passed=(independent_pass(row,80) and row['stress_pf'] is not None and row['stress_pf']>=1
                and metrics['passed'])
        results.append({'candidate':cid,'training':row,'metrics':metrics,'passed':bool(passed)})
        write_json(out/'development-a.json',{'candidates':results})
        print('A',cid,passed,metrics['regular']['pf'],flush=True)
    selected=select_a(results)
    if selected is None:
        manifest.update(status='no_profitable_development_candidate',
                        reason='No candidate passed training and development A; B and reserved performance unopened in this iteration')
        write_json(out/'manifest.json',manifest);print(manifest['reason'],flush=True);return
    cid=selected['candidate'];params,target,hold=configs[cid]
    frozen={'candidate':cid,'parameters':asdict(params),'target_r':target,'hold_minutes':hold,
            'development_a':selected,'frozen_at':pd.Timestamp.now(tz='UTC').isoformat()}
    write_json(out/'frozen-candidate.json',frozen)
    b=measure_portfolio(out,'selected-b',frames,funding,
        period_events(events_by_id[cid],SPLIT_B,END,hold),target,hold,SPLIT_B,END)
    passed=b['passed'] and selected['metrics']['regular']['trades']+b['regular']['trades']>=100
    frozen['development_b']=b;frozen['passed']=bool(passed);write_json(out/'frozen-candidate.json',frozen)
    manifest.update(status='development_passed_requires_jev_and_reserved' if passed else 'development_b_failed')
    write_json(out/'manifest.json',manifest);print(manifest['status'],flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('user_data/data/okx_scalp'))
    parser.add_argument('--output-dir',type=Path,default=Path('user_data/backtest_results/cost-aware-20260927'))
    args=parser.parse_args()
    try: run(args)
    except Exception as error:
        path=args.output_dir/'manifest.json'
        if path.exists():
            import json
            state=json.loads(path.read_text())
            if state.get('status')=='development':
                state.update(status='failed',error_type=type(error).__name__);write_json(path,state)
        raise
if __name__=='__main__': main()
