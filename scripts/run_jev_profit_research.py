#!/usr/bin/env python3
"""Profit-first development, frozen cross-instrument audit, and honest no-go gates."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from itertools import product
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from scripts.run_jev_ma_research import fingerprint, load_key_file, predict
from scripts.run_jev_scalp_research import period_events, save_portfolio, stats as legacy_stats
from scripts.run_okx_v2_research import write_json
from user_data.strategy_lib.jev_provider import JevClient
from user_data.strategy_lib.jev_research import event_key
from user_data.strategy_lib.profit_signal_engine import ProfitParameters, scan_profit, profit_events
from user_data.strategy_lib.scalp_paths import evaluate_events
from user_data.strategy_lib.v2_backtester import BacktestOptions, simulate_portfolio, summarize_backtest

DEV_SYMBOLS = tuple(f'{x}-USDT-SWAP' for x in ['BTC','ETH','SOL','XRP','DOGE','ADA','LINK','AVAX'])
AUDIT_SYMBOLS = tuple(f'{x}-USDT-SWAP' for x in ['SUI','NEAR','UNI','AAVE','LTC','BNB','DOT','ATOM'])
START, SPLIT_A, SPLIT_B, END = [pd.Timestamp(x, tz='UTC') for x in ['2026-07-31','2026-08-27','2026-09-11','2026-09-26']]
AUDIT_START = pd.Timestamp('2026-08-11', tz='UTC')


def stats(rows):
    result = legacy_stats(rows)
    if not rows.empty and not (rows.net_return < 0).any() and (rows.net_return > 0).any():
        result['pf'] = float('inf')  # JSON export uses null; never invent a finite PF.
    return result


def choose_threshold(thresholds):
    qualifying = [r for r in thresholds if independent_pass(r)]
    pool = qualifying or [r for r in thresholds if r['n'] >= 30] or thresholds
    chosen = max(pool, key=lambda r: (r['pf'] or 0, r['n']))
    return chosen, bool(qualifying)


def load_dataset(directory, symbols, start):
    frames, funding, hashes = {}, {}, {}
    for symbol in symbols:
        path = directory / f'{symbol}-5m.feather'
        frame = pd.read_feather(path).sort_values('date').reset_index(drop=True)
        frame['date'] = pd.to_datetime(frame.date, utc=True)
        if frame.empty or frame.date.iloc[0] > start-pd.Timedelta(hours=24):
            raise ValueError('insufficient warmup')
        expected = pd.date_range(frame.date.iloc[0], END-pd.Timedelta(minutes=5), freq='5min')
        if not pd.DatetimeIndex(frame.date).equals(expected):
            raise ValueError('incomplete 5m data')
        values = frame[['open','high','low','close','volume','quote_volume']].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values[:,:4] <= 0).any() or (values[:,4:] < 0).any():
            raise ValueError('invalid prices or volume')
        if (frame.high < frame[['open','low','close']].max(axis=1)).any() or (frame.low > frame[['open','high','close']].min(axis=1)).any():
            raise ValueError('invalid OHLC')
        fp = directory/f'{symbol}-funding.feather'
        fund = pd.read_feather(fp)
        fund['date'] = pd.to_datetime(fund.date, utc=True)
        if fund.empty or not np.isfinite(fund.rate.astype(float)).all():
            raise ValueError('missing or invalid observed funding')
        frames[symbol], funding[symbol] = frame, fund
        hashes[symbol] = {'candles':fingerprint(path), 'funding':fingerprint(fp)}
    return frames, funding, hashes


def independent_pass(result, minimum=30):
    return result['n'] >= minimum and result['pf'] is not None and result['pf'] >= 1.2 and result['mean_net_return'] > 0


def portfolio_pass(result, stress, minimum=30):
    return (result['trades'] >= minimum and result['pf'] is not None and result['pf'] >= 1.2
            and result['final_equity'] > 100 and result['drawdown'] <= .2
            and stress['trades'] > 0 and stress['pf'] is not None and stress['pf'] >= 1 and stress['final_equity'] >= 100)


def label(frames, funding, events, target, hold, stress=False):
    return evaluate_events(frames, events, target, hold,
                           fee_rate=.001 if stress else .0005,
                           slippage_rate=.001 if stress else .0005,
                           funding_rate_per_8h=.0002 if stress else .0001,
                           funding_frames=funding)


def portfolio(frames, funding, events, target, hold, start, end, stress=False):
    options = BacktestOptions(exit_mode='fixed3', take_profit_r=target, max_hold_minutes=hold, candle_minutes=5,
                               fee_rate=.001 if stress else .0005, slippage_rate=.001 if stress else .0005,
                               missing_funding_rate_per_8h=.0002 if stress else .0001)
    return simulate_portfolio(frames, events, options, start=start, end=end, funding_frames=funding)


def event_set(signals, target):
    return sorted([e for pair, frame in signals.items() for e in profit_events(pair, frame, target)],
                  key=lambda e:(e.date,e.pair,e.side))


def model_state(event, signals, params, target, hold, funding):
    past = signals.loc[signals.decision_at <= event.date]
    if past.empty or past.iloc[-1].decision_at != event.date:
        raise ValueError('missing causal decision row')
    last = past.iloc[-1]
    ref = float(last.close)
    tail = past.tail(24)
    vol = float(tail.volume.mean())
    bars = [{**{key:round(float(getattr(row,key))/ref-1,8) for key in ['open','high','low','close']},
             'volume_ratio':round(float(row.volume)/vol,5) if vol else 0.} for row in tail.itertuples()]
    rates = funding.loc[funding.date <= event.date]
    return {
        'strategy':asdict(params), 'side':event.side, 'bars_5m_oldest_first':bars,
        'features':{key:float(last[key])/ref for key in ['fast','slow','atr14','bias15','bias60']},
        'compression_atr':float(last.compression),
        'last_known_funding_rate':float(rates.iloc[-1].rate) if not rates.empty else None,
        'execution_rules':{
            'entry':'next 5m opening price, not provided; adverse slippage 0.0005',
            'fixed_stop_price_divided_by_last_closed_price':event.stop_price/ref,
            'target_r':target, 'r_definition':'distance between actual slipped entry fill and fixed stop',
            'maximum_hold_minutes':hold, 'time_exit':'close of final complete 5m candle within holding interval',
            'fee_each_side':.0005, 'slippage_each_side':.0005,
            'funding':'actual future funding settlements are unknown; account for cost, latest historical rate provided',
            'path_rule':'stop first if both stop and target touch; gap stop fills at worse opening; no pyramids',
            'outcome':'strictly positive net PnL after all execution costs and funding'
        }
    }


def score_predictions(client, signals, funding, events, params, target, hold, cache, output, name):
    answers = {}
    def assess(event):
        state = model_state(event, signals[event.pair], params, target, hold, funding[event.pair])
        return event_key(event), predict(client,state,cache)[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,(key,value) in enumerate(pool.map(assess,events)):
            answers[key] = value
            if (i+1)%20 == 0:
                print(f'{name} Jev {i+1}/{len(events)}',flush=True)
                write_json(output/f'{name}-predictions.json',answers)
    write_json(output/f'{name}-predictions.json',answers)
    return answers


def measure_portfolio(out, prefix, frames, funding, events, target, hold, begin, end):
    regular = portfolio(frames,funding,events,target,hold,begin,end)
    stressed = portfolio(frames,funding,events,target,hold,begin,end,True)
    base = save_portfolio(out,prefix,regular)
    stress = save_portfolio(out,prefix+'-stress',stressed)
    return {'regular':base, 'stress':stress, 'passed':portfolio_pass(base,stress)}


def run(args):
    out = args.output_dir
    if out.exists() and any(out.iterdir()):
        raise ValueError('output directory must be empty')
    out.mkdir(parents=True,exist_ok=True)
    sources = [Path(__file__).resolve(), ROOT/'user_data/strategy_lib/profit_signal_engine.py',
               ROOT/'user_data/strategy_lib/scalp_paths.py',ROOT/'user_data/strategy_lib/v2_backtester.py',
               ROOT/'user_data/strategy_lib/jev_provider.py']
    manifest = {'status':'development', 'live_claim_allowed':False, 'heldout_prices_opened':False,
                'development_symbols':DEV_SYMBOLS, 'audit_symbols':AUDIT_SYMBOLS,
                'protocol_sha256':fingerprint(ROOT/'docs/research/JEV_PROFIT_PROTOCOL.md'),
                'code_sha256':{str(p.relative_to(ROOT)):fingerprint(p) for p in sources},
                'grid_trials':192, 'audit_kind':'cross-instrument historical; not future chronological evidence',
                'created_at':pd.Timestamp.now(tz='UTC').isoformat()}
    write_json(out/'manifest.json',manifest)
    frames,funding,hashes = load_dataset(args.data_dir,DEV_SYMBOLS,START)
    manifest['development_data_sha256'] = hashes
    write_json(out/'manifest.json',manifest)
    configs,events_by_id,signal_cache,training = {},{},{},[]
    for family,ma,volume,buffer in product(['breakout_retest','trend_pullback','sweep_reclaim','range_reversion'],
                                           [(9,21),(20,60)],[1.,1.5],[.15,.3]):
        params = ProfitParameters(family=family,fast=ma[0],slow=ma[1],volume_multiple=volume,stop_buffer_atr=buffer)
        signals = {pair:scan_profit(frame,params) for pair,frame in frames.items()}
        key = json.dumps(asdict(params),sort_keys=True)
        signal_cache[key] = signals
        for target,hold in product([1.5,2.,3.],[30,60]):
            cid = len(configs)
            configs[cid] = (params,target,hold,key)
            events = event_set(signals,target)
            events_by_id[cid] = events
            rows = label(frames,funding,period_events(events,START,SPLIT_A,hold),target,hold)
            training.append({'candidate':cid,**asdict(params),'target_r':target,'hold_minutes':hold,**stats(rows)})
        print(f'development training {len(training)}/192',flush=True)
    pd.DataFrame(training).to_csv(out/'training-grid.csv',index=False)
    def train_rank(r):
        return (r['n']>=80,independent_pass(r,80),r['pf'] or 0,r['n'])
    shortlist = sorted(training,key=train_rank,reverse=True)[:12]
    validation = []
    for candidate in shortlist:
        cid = candidate['candidate'];params,target,hold,_ = configs[cid]
        row = {'candidate':cid}
        for name,begin,end in [('a',SPLIT_A,SPLIT_B),('b',SPLIT_B,END)]:
            events = period_events(events_by_id[cid],begin,end,hold)
            regular = stats(label(frames,funding,events,target,hold))
            stress = stats(label(frames,funding,events,target,hold,True))
            row[name] = regular;row[name+'_stress'] = stress
        row['passed'] = (all(independent_pass(row[k]) and row[k+'_stress']['pf'] is not None and row[k+'_stress']['pf']>=1
                             for k in ['a','b']) and row['a']['n']+row['b']['n']>=100)
        validation.append(row)
    write_json(out/'validation-shortlist.json',{'candidates':validation})
    def validation_rank(r):
        enough = min(r['a']['n'],r['b']['n']) >= 30
        return (enough,r['passed'],min(r['a']['pf'] or 0,r['b']['pf'] or 0))
    finalists = sorted(validation,key=validation_rank,reverse=True)[:6]
    finalists_metrics = []
    for candidate in finalists:
        cid = candidate['candidate'];params,target,hold,_ = configs[cid]
        record = {'candidate':cid}
        for name,begin,end in [('a',SPLIT_A,SPLIT_B),('b',SPLIT_B,END)]:
            record[name] = measure_portfolio(out,f'candidate-{cid}-{name}',frames,funding,
                period_events(events_by_id[cid],begin,end,hold),target,hold,begin,end)
        record['passed'] = (candidate['passed'] and record['a']['passed'] and record['b']['passed']
                            and record['a']['regular']['trades']+record['b']['regular']['trades']>=100)
        finalists_metrics.append(record)
        print('portfolio finalist',cid,record['passed'],flush=True)
    write_json(out/'portfolio-finalists.json',{'candidates':finalists_metrics})
    selected = max(finalists_metrics,key=lambda r:(r['passed'],
                   min(r['a']['regular']['trades'],r['b']['regular']['trades'])>=30,
                   min(r['a']['regular']['pf'] or 0,r['b']['regular']['pf'] or 0)))
    cid = selected['candidate'];params,target,hold,key = configs[cid]
    events = events_by_id[cid];signals = signal_cache[key]
    frozen = {'candidate':cid,'parameters':asdict(params),'target_r':target,'hold_minutes':hold,
              'baseline_development':selected,'baseline_passed':selected['passed'],
              'frozen_at':pd.Timestamp.now(tz='UTC').isoformat()}
    write_json(out/'frozen-candidate.json',frozen)
    print('candidate frozen',json.dumps(frozen),flush=True)
    load_key_file(args.env_file)
    client = JevClient(model='jev-1.13.0',timeout=30)
    cache = Path('user_data/backtest_results/jev-profit-cache')
    jev_metrics = {}
    cutoff = .5
    cutoff_passed = False
    for name,begin,end in [('a',SPLIT_A,SPLIT_B),('b',SPLIT_B,END)]:
        subset = period_events(events,begin,end,hold)
        answers = score_predictions(client,signals,funding,subset,params,target,hold,cache,out,name)
        rows = label(frames,funding,subset,target,hold)
        rows['probability'] = [answers[f'{r.pair}|{r.date.isoformat()}|{r.side}']['probability'] for r in rows.itertuples()]
        rows.to_csv(out/f'{name}-labels.csv',index=False)
        if name == 'a':
            thresholds = [{'threshold':t,**stats(rows.loc[rows.probability>=t])} for t in [.15,.25,.35,.45,.55,.65]]
            chosen, cutoff_passed = choose_threshold(thresholds)
            cutoff = chosen['threshold']
            frozen['jev_threshold'] = {'value':cutoff,'development_a':chosen,'candidates':thresholds,
                                      'selected_at':pd.Timestamp.now(tz='UTC').isoformat()}
            write_json(out/'frozen-candidate.json',frozen)
        accepted = [e for e in subset if answers[event_key(e)]['probability']>=cutoff]
        jev_metrics[name] = measure_portfolio(out,f'jev-{name}',frames,funding,accepted,target,hold,begin,end)
        jev_metrics[name]['signals'] = len(subset)
        jev_metrics[name]['accepted_signals'] = len(accepted)
    jev_passed = (cutoff_passed and jev_metrics['a']['passed'] and jev_metrics['b']['passed']
                  and jev_metrics['a']['regular']['trades']+jev_metrics['b']['regular']['trades']>=100)
    frozen['jev_development'] = jev_metrics;frozen['jev_passed'] = jev_passed
    frozen['all_decisions_frozen_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    write_json(out/'frozen-candidate.json',frozen)
    if not frozen['baseline_passed'] and not jev_passed:
        manifest.update(status='no_profitable_development_candidate',heldout_prices_opened=False,
                        failed_reason='neither baseline nor Jev passed both development portfolio and stress gates')
        write_json(out/'manifest.json',manifest)
        print('NO GO: development failed; reserved prices remain unopened',flush=True)
        return
    use_jev = jev_passed and (not frozen['baseline_passed'] or
               jev_metrics['b']['stress']['pf'] >= selected['b']['stress']['pf'])
    frozen['audit_route'] = 'jev' if use_jev else 'baseline'
    frozen['audit_route_frozen_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    write_json(out/'frozen-candidate.json',frozen)
    # This is intentionally the first read of reserved price data in the runner.
    manifest.update(status='cross_instrument_audit',heldout_prices_opened=True,
                    reserved_opened_at=pd.Timestamp.now(tz='UTC').isoformat(),frozen_sha256=fingerprint(out/'frozen-candidate.json'))
    write_json(out/'manifest.json',manifest)
    audit_frames,audit_funding,audit_hashes = load_dataset(args.audit_dir,AUDIT_SYMBOLS,AUDIT_START)
    manifest['audit_data_sha256'] = audit_hashes
    write_json(out/'manifest.json',manifest)
    audit_signals = {pair:scan_profit(frame,params) for pair,frame in audit_frames.items()}
    audit_events = period_events(event_set(audit_signals,target),AUDIT_START,END,hold)
    if use_jev:
        answers = score_predictions(client,audit_signals,audit_funding,audit_events,params,target,hold,cache,out,'audit')
        accepted = [e for e in audit_events if answers[event_key(e)]['probability']>=cutoff]
    else:
        accepted = audit_events
    audit = measure_portfolio(out,'audit-selected',audit_frames,audit_funding,accepted,target,hold,AUDIT_START,END)
    # Delayed entry uses original pre-decision stop; no re-estimation from future prices.
    delayed = [replace(e,date=e.date+pd.Timedelta(minutes=5)) for e in accepted if e.date+pd.Timedelta(minutes=hold+5)<=END]
    audit['delayed_5m'] = measure_portfolio(out,'audit-delay',audit_frames,audit_funding,delayed,target,hold,AUDIT_START,END)
    audit['passed'] = portfolio_pass(audit['regular'],audit['stress'],100)
    audit['signals'] = len(audit_events);audit['accepted_signals'] = len(accepted)
    write_json(out/'audit-metrics.json',audit)
    manifest.update(status='historical_audit_passed' if audit['passed'] else 'historical_audit_failed',
                    future_forward_validation_complete=False)
    write_json(out/'manifest.json',manifest)
    print('FINAL',json.dumps(manifest),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('user_data/data/okx_scalp'))
    parser.add_argument('--audit-dir',type=Path,default=Path('user_data/data/okx_profit_holdout'))
    parser.add_argument('--output-dir',type=Path,default=Path('user_data/backtest_results/jev-profit-20260926'))
    parser.add_argument('--env-file',type=Path)
    args = parser.parse_args()
    try:
        run(args)
    except Exception as error:
        manifest_path = args.output_dir/'manifest.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('status') in {'development','cross_instrument_audit'}:
                manifest.update(status='failed',error_type=type(error).__name__,error_reason=getattr(error,'reason',''))
                write_json(manifest_path,manifest)
        raise

if __name__ == '__main__':
    main()
