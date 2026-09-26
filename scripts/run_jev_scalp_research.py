#!/usr/bin/env python3
"""Train/validation/frozen-holdout research on real 5m bars; no live orders."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from itertools import product
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from scripts.run_okx_v2_research import write_json
from scripts.run_jev_ma_research import load_key_file, predict, fingerprint
from user_data.strategy_lib.jev_provider import JevClient, JevProviderError
from user_data.strategy_lib.jev_research import select_events,event_key,calibration
from user_data.strategy_lib.scalp_signal_engine import ScalpParameters,scan_scalp,signal_events
from user_data.strategy_lib.scalp_paths import evaluate_events
from user_data.strategy_lib.v2_backtester import BacktestOptions,simulate_portfolio,summarize_backtest


def stats(rows):
    if rows.empty:return {'n':0,'win_rate':None,'pf':None,'mean_net_return':None}
    pnls=rows.net_return.astype(float); losses=-pnls[pnls<0].sum(); wins=pnls[pnls>0].sum()
    return {'n':len(rows),'win_rate':float((pnls>0).mean()),'pf':float(wins/losses) if losses else (999. if wins else 0.),'mean_net_return':float(pnls.mean())}


def gate(s,minimum):
    return s['n']>=minimum and s['win_rate']>.5 and s['pf']>=1.1 and s['mean_net_return']>0


def rank(s,minimum):
    if s['n']<minimum:return (-1,s['n'],0.)
    return (int(gate(s,minimum)),s['pf'],s['win_rate'])


def period_events(events,start,end,hold):
    # Purge the full maximum duration before each boundary.
    return [e for e in events if start<=e.date and e.date+pd.Timedelta(minutes=hold)<=end]


def scalp_state(event,frame,params,target,hold):
    past=frame.loc[frame.date+pd.Timedelta(minutes=5)<=event.date].copy()
    if len(past)<120 or past.iloc[-1].date+pd.Timedelta(minutes=5)!=event.date:
        raise ValueError('incomplete past state')
    f=scan_scalp(past,params);last=f.iloc[-1];ref=float(last.close)
    mean=float(f.tail(24).volume.mean())
    bars=[{**{k:round(float(getattr(r,k))/ref-1,7) for k in ['open','high','low','close']},'volume_ratio':round(float(r.volume)/mean,4) if mean else 0.} for r in f.tail(24).itertuples()]
    return {'strategy':{'description':'5m dual EMA compression then trend breakout or first pullback; 15m EMA9/21 bias',**asdict(params)},
            'side':event.side,'bars_5m_oldest_first':bars,
            'features':{'fast_vs_price':float(last.fast)/ref-1,'slow_vs_price':float(last.slow)/ref-1,'atr_fraction':float(last.atr14)/ref,'compression_atr':float(last.compression),'higher_15m_bias_fraction':float(last.bias)/ref},
            'stop_distance_fraction':abs(ref-event.stop_price)/ref,
            'execution_rules':{'entry':'next 5m open, not known yet, with 0.05% adverse slippage',
                'stop':'fixed '+str(round(event.stop_price/ref-1,7))+' relative to the last closed reference price (price/reference - 1)',
                'target_r':target,'target_definition':'R is the distance from actual slipped entry fill to fixed stop',
                'maximum_holding_minutes':hold,'time_exit':'close of last 5m bar within holding interval; e.g. 15min exit at close of third bar',
                'fee_each_side':.0005,'slippage_each_side':.0005,'funding':'adverse 0.01% at UTC 00/08/16 for positions already open',
                'same_bar_ambiguity':'stop first, then target; stop gap filled at worse open',
                'success':'strictly positive net profit after all costs; no pyramids'}}


def portfolio(frames,events,target,hold,start,end,stress=False):
    options=BacktestOptions(exit_mode='fixed3',take_profit_r=target,max_hold_minutes=hold,candle_minutes=5,
                            fee_rate=.001 if stress else .0005,slippage_rate=.001 if stress else .0005,
                            missing_funding_rate_per_8h=.0002 if stress else .0001)
    return simulate_portfolio(frames,events,options,start=start,end=end)


def save_portfolio(out,name,result):
    pd.DataFrame([asdict(t) for t in result.trades]).to_csv(out/f'{name}-trades.csv',index=False)
    result.equity_curve.to_csv(out/f'{name}-equity.csv',index=False)
    summary=summarize_backtest(result)
    if not result.trades:summary.update(win_rate=None,pf=None)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,default=Path('user_data/data/okx_scalp'))
    p.add_argument('--output-dir',type=Path,default=Path('user_data/backtest_results/jev-scalp-20260926'))
    p.add_argument('--start',default='2026-07-31');p.add_argument('--validation',default='2026-08-27');p.add_argument('--holdout',default='2026-09-11');p.add_argument('--end',default='2026-09-26')
    p.add_argument('--env-file',type=Path);p.add_argument('--jev-limit',type=int,default=0)
    args=p.parse_args();out=args.output_dir
    if out.exists() and any(out.iterdir()):raise ValueError('use an empty output directory')
    out.mkdir(parents=True,exist_ok=True)
    start,validation,holdout,end=[pd.Timestamp(v,tz='UTC') for v in (args.start,args.validation,args.holdout,args.end)]
    if not start<validation<holdout<end:raise ValueError('invalid split dates')
    if args.jev_limit<0:raise ValueError('negative sample size')
    frames={}
    for path in sorted(args.data_dir.glob('*-5m.feather')):
        f=pd.read_feather(path);f.date=pd.to_datetime(f.date,utc=True)
        f=f.sort_values('date').reset_index(drop=True)
        expected=pd.date_range(f.date.iloc[0],end-pd.Timedelta(minutes=5),freq='5min')
        if not pd.DatetimeIndex(f.date).equals(expected) or f.date.iloc[0]>start-pd.Timedelta(hours=12):
            raise ValueError('5m data incomplete or insufficient warmup')
        ohlc=f[['open','high','low','close']].to_numpy(dtype=float)
        if not np.isfinite(ohlc).all() or (ohlc<=0).any() or (f.high<f[['open','close','low']].max(axis=1)).any() or (f.low>f[['open','close','high']].min(axis=1)).any():
            raise ValueError('invalid source OHLC')
        frames[path.name.replace('-5m.feather','')]=f
    if not frames:raise ValueError('no 5m data')
    manifest={'status':'training','splits':dict(start=str(start),validation=str(validation),holdout=str(holdout),end=str(end)),
              'data_hashes':{p.name:fingerprint(p) for p in args.data_dir.glob('*-5m.feather')},
              'code_hashes':{str(p.relative_to(ROOT)):fingerprint(p) for p in [Path(__file__).resolve(),ROOT/'user_data/strategy_lib/scalp_signal_engine.py',ROOT/'user_data/strategy_lib/scalp_paths.py',ROOT/'user_data/strategy_lib/v2_backtester.py']},
              'costs':{'fee':.0005,'slippage':.0005,'adverse_funding_each_8h':.0001},'live_claim_allowed':False,
              'grid_trials':144,'train_minimum':100,'validation_minimum':50,'holdout_minimum':100,
              'win_rate_target':'>0.50 net of costs','profit_factor_target':1.1,'jev_limit':args.jev_limit}
    write_json(out/'manifest.json',manifest)
    configs={};event_cache={};training=[]
    for fastslow,mode,coil,stop in product([(9,21),(20,60)],['breakout','pullback'],[.5,1.],[1.5,2.5]):
        params=ScalpParameters(fast=fastslow[0],slow=fastslow[1],entry_mode=mode,compression_atr=coil,stop_atr=stop)
        signals={pair:scan_scalp(f,params) for pair,f in frames.items()}
        for target,hold in product([.75,1.,1.5],[15,30,60]):
            cid=len(configs);configs[cid]=(params,target,hold)
            events=sorted([e for pair,s in signals.items() for e in signal_events(pair,s,target)],key=lambda e:(e.date,e.pair))
            # Fixed liquidity universe; simultaneous ties deterministic by pair, no future ranking.
            event_cache[cid]=events
            subset=period_events(events,start,validation,hold)
            rows=evaluate_events(frames,subset,target,hold)
            training.append({'candidate':cid,**asdict(params),'target_r':target,'hold_minutes':hold,**stats(rows)})
        print(f'training {len(training)}/144',flush=True)
    pd.DataFrame(training).to_csv(out/'training-grid.csv',index=False)
    ranked=sorted(training,key=lambda r:rank(r,100),reverse=True)
    shortlist=ranked[:12]
    validations=[]
    for c in shortlist:
        cid=c['candidate'];params,target,hold=configs[cid]
        events=period_events(event_cache[cid],validation,holdout,hold)
        rows=evaluate_events(frames,events,target,hold)
        validations.append({'candidate':cid,**stats(rows)})
    pd.DataFrame(validations).to_csv(out/'validation-shortlist.csv',index=False)
    chosen=max(validations,key=lambda r:rank(r,50))
    cid=chosen['candidate'];params,target,hold=configs[cid];events=event_cache[cid]
    frozen={'candidate':cid,'parameters':asdict(params),'target_r':target,'hold_minutes':hold,
            'training':next(r for r in training if r['candidate']==cid),'validation':chosen,
            'selection_passed':gate(chosen,50),'selection':'144 training candidates -> top12 validation -> one frozen; no holdout optimization',
            'frozen_at':pd.Timestamp.now(tz='UTC').isoformat()}
    write_json(out/'frozen-strategy.json',frozen)
    print('FROZEN',json.dumps(frozen),flush=True)
    # Calibrate a Jev cutoff on validation only, then freeze before revealing holdout labels.
    load_key_file(args.env_file);client=JevClient(model='jev-1.13.0',timeout=30)
    cache=Path('user_data/backtest_results/jev-scalp-cache')
    sampled={};predictions={};independent={};results={}
    for name,begin,finish in [('validation',validation,holdout),('holdout',holdout,end)]:
        subset=period_events(events,begin,finish,hold)
        selected=subset if args.jev_limit==0 else subset[:args.jev_limit];sampled[name]=selected
        def assess(e):return event_key(e),predict(client,scalp_state(e,frames[e.pair],params,target,hold),cache)[0]
        answers={}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i,(key,answer) in enumerate(pool.map(assess,selected)):
                answers[key]=answer
                if (i+1)%20==0:print(f'{name} Jev {i+1}/{len(selected)}',flush=True)
        predictions[name]=answers;write_json(out/f'{name}-predictions.json',answers)
        rows=evaluate_events(frames,selected,target,hold)
        write_json(out/f'{name}-label-skips.json',dict(rows.attrs))
        rows['probability']=[answers[f'{r.pair}|{pd.Timestamp(r.date).isoformat()}|{r.side}']['probability'] for r in rows.itertuples()]
        independent[name]=rows;rows.to_csv(out/f'{name}-sample-labels.csv',index=False)
        if name=='validation':
            thresholds=[{'threshold':float(t),**stats(rows.loc[rows.probability>=t])} for t in [.15,.2,.25,.3,.35,.4,.45,.5,.55]]
            qualifying=[r for r in thresholds if gate(r,50)]
            # When no cutoff meets both win-rate and profit gates, keep the best diagnostic
            # with at least 50 validation labels, and explicitly mark that it did not pass.
            pool=qualifying or [r for r in thresholds if r['n']>=50]
            selected_cutoff=max(pool,key=lambda r:rank(r,50)) if pool else {'threshold':.5,'n':0,'win_rate':None,'pf':None,'mean_net_return':None}
            cutoff=selected_cutoff['threshold']
            frozen['jev']={'threshold':cutoff,'validation':selected_cutoff,'validation_gate_passed':bool(qualifying),'threshold_candidates':thresholds,'frozen_at':pd.Timestamp.now(tz='UTC').isoformat()}
            write_json(out/'frozen-strategy.json',frozen)
            print('JEV THRESHOLD FROZEN',cutoff,flush=True)
        accepted=[e for e in selected if answers[event_key(e)]['probability']>=cutoff]
        full_result=portfolio(frames,subset,target,hold,begin,finish)
        pair_base=portfolio(frames,selected,target,hold,begin,finish)
        jev_result=portfolio(frames,accepted,target,hold,begin,finish)
        stress=portfolio(frames,accepted,target,hold,begin,finish,stress=True)
        baseline_stress=portfolio(frames,subset,target,hold,begin,finish,stress=True)
        results[name]={'full_baseline':save_portfolio(out,f'{name}-full',full_result),
                       'sample_baseline':save_portfolio(out,f'{name}-sample',pair_base),
                       'jev':save_portfolio(out,f'{name}-jev',jev_result),
                       'jev_stress':summarize_backtest(stress),'full_stress':summarize_backtest(baseline_stress),
                       'prediction_covers_full_period':len(selected)==len(subset),'sample_signals':len(selected),'accepted_signals':len(accepted),
                       'independent_sample':stats(rows),
                       'independent_jev':stats(rows.loc[rows.probability>=cutoff]),
                       'calibration':calibration(rows.probability.tolist(),(rows.net_return>0).tolist())}
        write_json(out/'metrics.json',results)
    h=results['holdout'];baseline=h['full_baseline'];jev=h['jev']
    def passed(s,stress):
        return s['trades']>=100 and (s['win_rate'] or 0)>.5 and (s['pf'] or 0)>=1.1 and s['final_equity']>100 and s['drawdown']<=.2 and stress['pf']>=1.
    manifest.update(status='completed',baseline_passed=passed(baseline,h['full_stress']) and frozen['selection_passed'],
                    jev_passed=passed(jev,h['jev_stress']) and frozen['jev']['validation_gate_passed'] and h['prediction_covers_full_period'],
                    finished_at=pd.Timestamp.now(tz='UTC').isoformat())
    write_json(out/'manifest.json',manifest)
    print('COMPLETE',json.dumps({'baseline':baseline,'jev':jev,'status':manifest},default=str),flush=True)

if __name__=='__main__':main()
