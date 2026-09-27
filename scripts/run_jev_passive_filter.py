#!/usr/bin/env python3
"""Train-only Jev cutoff selection for a fixed passive order hypothesis."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import pandas as pd
from scripts.run_jev_ma_research import load_key_file,predict
from scripts.run_jev_profit_research import (DEV_SYMBOLS,START,SPLIT_A,SPLIT_B,END,load_dataset,
    model_state,fingerprint,write_json)
from scripts.run_passive_research import make_orders,window,measure
from user_data.strategy_lib.cost_aware_signal_engine import CostAwareParameters,scan_cost_aware
from user_data.strategy_lib.jev_provider import JevClient
from user_data.strategy_lib.jev_research import event_key

PARAMS=CostAwareParameters('breakout',3.,.004,0.)
OFFSET,TARGET,HOLD=.25,2.,60
THRESHOLDS=(.15,.25,.35,.45,.55,.65)


def state_for_order(order,signals,funding,target_r=TARGET,hold_minutes=HOLD,params=PARAMS):
    state=model_state(order,signals,params,target_r,hold_minutes,funding)
    ref=float(signals.loc[signals.decision_at<=order.date].iloc[-1].close)
    if params.family=='trend_pullback':
        state['hypothesis']='passive-limit-net-profit-conditional-on-fill-trend-pullback-target-1r-hold-30m-v1'
    elif params.family=='range_reversion':
        state['hypothesis']='passive-limit-net-profit-conditional-on-fill-range-reversion-ema9-21-atr003-target-1r-hold-30m-v1'
    elif params.family=='retest':
        state['hypothesis']='passive-limit-net-profit-conditional-on-fill-retest-target-1r-hold-30m-v1'
    elif target_r==1. and hold_minutes==30:
        state['hypothesis']='passive-limit-net-profit-conditional-on-fill-target-1r-hold-30m-v1'
    elif target_r==1.:
        state['hypothesis']='passive-limit-net-profit-conditional-on-fill-target-1r-v1'
    else:
        state['hypothesis']='passive-limit-net-profit-conditional-on-fill-v1'
    state['execution_rules']={
        'outcome':'Estimate probability of strictly positive NET PnL CONDITIONAL ON execution under the hypothetical fill rules. Unfilled orders are excluded, not labelled losses. Future fill status is unknown.',
        'entry':'Fixed passive limit active only during the next 5m bar; no future opening price provided. Hypothetical full fill only if penetration occurs.',
        'limit_price_divided_by_last_closed_price':order.limit_price/ref,
        'fixed_stop_price_divided_by_last_closed_price':order.stop_price/ref,
        'post_only':'Cancel if next opening is at or below limit for long, or at or above limit for short.',
        'fill':'Long next-bar low <= limit*(1-0.0001); short next-bar high >= limit*(1+0.0001). Otherwise cancel unfilled.',
        'entry_slippage':0.,'entry_maker_fee':.0002,'exit_taker_fee':.0005,'exit_adverse_slippage':.0005,
        'target_r':target_r,'r_definition':'distance between fixed limit fill and fixed stop',
        'maximum_minutes_from_order_activation':hold_minutes,
        'path':'Entry bar may stop out but may NOT take profit. Later stop first if stop and target touch; gap stop fills at worse opening, then exit slippage. All exits are taker.',
        'time_exit':'Final complete 5m candle close by the fixed holding deadline',
        'funding':'Actual future signed settlements unknown; last historical rate provided. Missing UTC 00/08/16 rates imputed as 0.0001 adverse; entry bar no settlement.',
        'scope':'Independent trade only. No portfolio sizing or real queue position evidence.'}
    return state


def score(client,signals,funding,orders,cache,out,name,target_r=TARGET,hold_minutes=HOLD,params=PARAMS):
    answers={};hits=0
    def one(order):
        value,cached=predict(client,state_for_order(order,signals[order.pair],funding[order.pair],target_r,hold_minutes,params),cache)
        return event_key(order),value,cached
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,(key,value,cached) in enumerate(pool.map(one,orders)):
            answers[key]=value;hits+=int(cached)
            if (i+1)%20==0:
                write_json(out/f'{name}-predictions.json',answers);print(f'{name} Jev {i+1}/{len(orders)}',flush=True)
    write_json(out/f'{name}-predictions.json',answers)
    return answers,{'orders':len(orders),'cached':hits,'api_calls':len(orders)-hits}


def all_metrics(frames,funding,orders,out=None,prefix='',target_r=TARGET,hold_minutes=HOLD):
    metrics={}
    for name,stress,strict in [('regular',False,True),('cost_only_stress',True,False),('strict_stress',True,True)]:
        result,rows=measure(frames,funding,orders,target_r,hold_minutes,stress,strict)
        metrics[name]=result
        if out is not None:rows.to_csv(out/f'{prefix}-{name}-labels.csv',index=False)
    return metrics


def qualifies(metrics,minimum):
    r=metrics['regular']
    if not (r['n']>=minimum and r['pf'] is not None and r['pf']>=1.2 and r['mean_net_return']>0 and r['win_rate']>=.5):return False
    return all(metrics[k]['n']>=minimum and metrics[k]['pf'] is not None and metrics[k]['pf']>=1 and metrics[k]['mean_net_return']>=0
               for k in ['cost_only_stress','strict_stress'])


def choose_threshold(records):
    passed=[r for r in records if r['passed']]
    if not passed:return None
    return max(passed,key=lambda r:(min(r['metrics'][k]['pf'] for k in ['cost_only_stress','strict_stress']),r['metrics']['regular']['pf']))


def study_windows(args):
    if getattr(args,'long_history_range_reversion_1r_hold_30m',False):
        if (getattr(args,'extended_training',False) or getattr(args,'long_history',False)
                or getattr(args,'long_history_target_1r',False)
                or getattr(args,'long_history_target_1r_hold_30m',False)
                or getattr(args,'long_history_retest_1r_hold_30m',False)
                or getattr(args,'long_history_trend_pullback_1r_hold_30m',False)):
            raise ValueError('choose one history mode')
        return (pd.Timestamp('2026-03-01',tz='UTC'),pd.Timestamp('2026-06-01',tz='UTC'),
                pd.Timestamp('2026-07-15',tz='UTC'),'JEV_LONG_HISTORY_RANGE_REVERSION_1R_HOLD_30M_PROTOCOL.md')
    if getattr(args,'long_history_trend_pullback_1r_hold_30m',False):
        if (getattr(args,'extended_training',False) or getattr(args,'long_history',False)
                or getattr(args,'long_history_target_1r',False)
                or getattr(args,'long_history_target_1r_hold_30m',False)
                or getattr(args,'long_history_retest_1r_hold_30m',False)):
            raise ValueError('choose one history mode')
        return (pd.Timestamp('2026-03-01',tz='UTC'),pd.Timestamp('2026-06-01',tz='UTC'),
                pd.Timestamp('2026-07-15',tz='UTC'),'JEV_LONG_HISTORY_TREND_PULLBACK_1R_HOLD_30M_PROTOCOL.md')
    if getattr(args,'long_history_retest_1r_hold_30m',False):
        if (getattr(args,'extended_training',False) or getattr(args,'long_history',False)
                or getattr(args,'long_history_target_1r',False)
                or getattr(args,'long_history_target_1r_hold_30m',False)):
            raise ValueError('choose one history mode')
        return (pd.Timestamp('2026-03-01',tz='UTC'),pd.Timestamp('2026-06-01',tz='UTC'),
                pd.Timestamp('2026-07-15',tz='UTC'),'JEV_LONG_HISTORY_RETEST_1R_HOLD_30M_PROTOCOL.md')
    if getattr(args,'long_history_target_1r_hold_30m',False):
        if (getattr(args,'extended_training',False) or getattr(args,'long_history',False)
                or getattr(args,'long_history_target_1r',False)):
            raise ValueError('choose one history mode')
        return (pd.Timestamp('2026-03-01',tz='UTC'),pd.Timestamp('2026-06-01',tz='UTC'),
                pd.Timestamp('2026-07-15',tz='UTC'),'JEV_LONG_HISTORY_TARGET_1R_HOLD_30M_PROTOCOL.md')
    if getattr(args,'long_history_target_1r',False):
        if getattr(args,'extended_training',False) or getattr(args,'long_history',False):
            raise ValueError('choose one history mode')
        return (pd.Timestamp('2026-03-01',tz='UTC'),pd.Timestamp('2026-06-01',tz='UTC'),
                pd.Timestamp('2026-07-15',tz='UTC'),'JEV_LONG_HISTORY_TARGET_1R_PROTOCOL.md')
    if getattr(args,'long_history',False):
        if getattr(args,'extended_training',False):raise ValueError('choose one history mode')
        return (pd.Timestamp('2026-03-01',tz='UTC'),pd.Timestamp('2026-06-01',tz='UTC'),
                pd.Timestamp('2026-07-15',tz='UTC'),'JEV_LONG_HISTORY_PROTOCOL.md')
    if getattr(args,'extended_training',False):return pd.Timestamp('2026-07-01',tz='UTC'),SPLIT_A,SPLIT_B,'JEV_EXTENDED_TRAINING_PROTOCOL.md'
    return START,SPLIT_A,SPLIT_B,'JEV_PASSIVE_FILTER_PROTOCOL.md'


def run(args):
    train_start,split_a,split_b,protocol=study_windows(args)
    retest_mode=getattr(args,'long_history_retest_1r_hold_30m',False)
    trend_pullback_mode=getattr(args,'long_history_trend_pullback_1r_hold_30m',False)
    range_reversion_mode=getattr(args,'long_history_range_reversion_1r_hold_30m',False)
    target_1r=(getattr(args,'long_history_target_1r',False)
               or getattr(args,'long_history_target_1r_hold_30m',False) or retest_mode or trend_pullback_mode or range_reversion_mode)
    target_r=1. if target_1r else TARGET
    hold_minutes=30 if getattr(args,'long_history_target_1r_hold_30m',False) or retest_mode or trend_pullback_mode or range_reversion_mode else HOLD
    params=(CostAwareParameters('range_reversion',3.,.003,0.,fast=9,slow=21) if range_reversion_mode
            else CostAwareParameters('trend_pullback',3.,.004,0.) if trend_pullback_mode
            else CostAwareParameters('retest',3.,.004,0.) if retest_mode else PARAMS)
    candidate=('60-range-reversion-ema9-21-atr003-target-1r-hold-30m' if range_reversion_mode
               else '59-trend-pullback-target-1r-hold-30m' if trend_pullback_mode
               else '59-retest-target-1r-hold-30m' if retest_mode
               else '59-target-1r-hold-30m' if hold_minutes==30 and target_1r
               else '59-target-1r' if target_r==1. else 59)
    out=args.output_dir
    if out.exists() and any(out.iterdir()):raise ValueError('output directory must be empty')
    out.mkdir(parents=True,exist_ok=True)
    sources=['scripts/run_jev_passive_filter.py','scripts/run_passive_research.py','scripts/run_jev_profit_research.py',
        'user_data/strategy_lib/passive_scalp_paths.py','user_data/strategy_lib/cost_aware_signal_engine.py','user_data/strategy_lib/jev_provider.py',
        'scripts/run_jev_ma_research.py','user_data/strategy_lib/scalp_signal_engine.py','user_data/strategy_lib/profit_signal_engine.py']
    manifest={'status':'training','heldout_prices_opened':False,'portfolio_verified':False,'live_claim_allowed':False,
        'model':'jev-1.13.0','candidate':candidate,'target_r':target_r,'hold_minutes':hold_minutes,
        'strategy_parameters':asdict(params),
        'training_start':train_start.isoformat(),
        'development_a_start':split_a.isoformat(),'development_b_start':split_b.isoformat(),
        'protocol_sha256':fingerprint(ROOT/'docs/research'/protocol),
        'filter_protocol_sha256':fingerprint(ROOT/'docs/research/JEV_PASSIVE_FILTER_PROTOCOL.md'),
        'code_sha256':{p:fingerprint(ROOT/p) for p in sources},'created_at':pd.Timestamp.now(tz='UTC').isoformat()}
    write_json(out/'manifest.json',manifest)
    frames,funding,hashes=load_dataset(args.data_dir,DEV_SYMBOLS,train_start)
    manifest['development_data_sha256']=hashes;write_json(out/'manifest.json',manifest)
    signals={pair:scan_cost_aware(frame,params) for pair,frame in frames.items()}
    orders=make_orders(signals,OFFSET,target_r)
    load_key_file(args.env_file);client=JevClient(model='jev-1.13.0',timeout=30)
    cache=ROOT/'user_data/backtest_results/jev-passive-cache'
    train=window(orders,train_start,split_a,hold_minutes)
    predictions,usage=score(client,signals,funding,train,cache,out,'training',target_r,hold_minutes,params)
    manifest['training_prediction_usage']=usage
    write_json(out/'training-baseline.json',all_metrics(frames,funding,train,out,'training-baseline',target_r,hold_minutes))
    trials=[]
    for threshold in THRESHOLDS:
        accepted=[o for o in train if predictions[event_key(o)]['probability']>=threshold]
        metrics=all_metrics(frames,funding,accepted,target_r=target_r,hold_minutes=hold_minutes)
        trials.append({'threshold':threshold,'accepted_orders':len(accepted),'metrics':metrics,'passed':qualifies(metrics,80)})
    write_json(out/'thresholds.json',{'trials':trials})
    chosen=choose_threshold(trials)
    if chosen is None:
        manifest.update(status='no_qualified_training_threshold',reason='No train-only Jev cutoff met sample, net win-rate, profitability and both stress gates')
        write_json(out/'manifest.json',manifest);print(manifest['status'],flush=True);return
    frozen={'candidate':candidate,'parameters':asdict(params),'offset_atr':OFFSET,'target_r':target_r,'hold_minutes':hold_minutes,
            'threshold':chosen['threshold'],'training':chosen,'frozen_at':pd.Timestamp.now(tz='UTC').isoformat()}
    write_json(out/'frozen-candidate.json',frozen)
    total=0
    for name,begin,end in [('a',split_a,split_b),('b',split_b,END)]:
        subset=window(orders,begin,end,hold_minutes)
        answers,usage=score(client,signals,funding,subset,cache,out,name,target_r,hold_minutes,params);manifest[f'{name}_prediction_usage']=usage
        accepted=[o for o in subset if answers[event_key(o)]['probability']>=chosen['threshold']]
        metrics=all_metrics(frames,funding,accepted,out,name,target_r,hold_minutes)
        baseline=all_metrics(frames,funding,subset,out,name+'-baseline',target_r,hold_minutes)
        write_json(out/f'{name}-metrics.json',{'baseline':baseline,'jev':metrics,'passed':qualifies(metrics,30)})
        total+=metrics['regular']['n']
        if not qualifies(metrics,30):
            manifest.update(status=f'development_{name}_failed');write_json(out/'manifest.json',manifest);print(manifest['status'],flush=True);return
    manifest.update(status='labels_passed_requires_portfolio_and_execution_validation' if total>=100 else 'combined_sample_count_failed')
    write_json(out/'manifest.json',manifest);print(manifest['status'],flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--long-history',action='store_true',help='Use frozen March/June/July long-history development windows')
    parser.add_argument('--long-history-target-1r',action='store_true',help='Use the frozen long-history 1R-target amendment')
    parser.add_argument('--long-history-target-1r-hold-30m',action='store_true',help='Use the frozen 1R-target, 30-minute-hold amendment')
    parser.add_argument('--long-history-retest-1r-hold-30m',action='store_true',help='Use the frozen retest, 1R-target, 30-minute-hold amendment')
    parser.add_argument('--long-history-trend-pullback-1r-hold-30m',action='store_true',help='Use the frozen trend-pullback, 1R-target, 30-minute-hold amendment')
    parser.add_argument('--long-history-range-reversion-1r-hold-30m',action='store_true',help='Use the frozen range-reversion, EMA9/21, 1R-target, 30-minute-hold amendment')
    parser.add_argument('--extended-training',action='store_true',help='Use the frozen July 1 training start with the expanded dataset')
    parser.add_argument('--data-dir',type=Path,default=ROOT/'user_data/data/okx_scalp')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'user_data/backtest_results/jev-passive-filter-20260927')
    parser.add_argument('--env-file',type=Path)
    args=parser.parse_args()
    try:run(args)
    except Exception as error:
        import json
        p=args.output_dir/'manifest.json'
        if p.exists():
            m=json.loads(p.read_text())
            if m['status']=='training':m.update(status='failed',error_type=type(error).__name__);write_json(p,m)
        raise
if __name__=='__main__':main()
