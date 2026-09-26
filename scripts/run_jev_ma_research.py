#!/usr/bin/env python3
"""Paired, outcome-blind Jev evaluation on existing MA entry engines (no orders)."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from scripts.run_okx_v2_research import (
    available_trade_pairs, build_events, clip_frame, load_frame, load_funding,
    load_snapshot, write_json,
)
from scripts.run_trend_compression_rolling_research import build_pair_events, rank_events
from user_data.strategy_lib.dual_ma_signal_engine import DualMaParameters
from user_data.strategy_lib.trend_compression_history import TrendCompressionParameters
from user_data.strategy_lib.okx_candles import resample_confirmed
from user_data.strategy_lib.v2_backtester import BacktestOptions, simulate_portfolio, summarize_backtest
from user_data.strategy_lib.jev_provider import JevClient, JevProviderError
from user_data.strategy_lib.jev_research import build_state, calibration, event_key, select_events


PROVIDER_HASH = hashlib.sha256((ROOT / 'user_data/strategy_lib/jev_provider.py').read_bytes()).hexdigest()

def load_key_file(path):
    """Read only relevant values; never source a shell file or print secrets."""
    if path is None:
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition('=')
        if sep and key in {'TYPESAFE_API_KEY', 'JEV_API_KEY'}:
            value = value.strip().strip('\"\'')
            if value and not os.environ.get(key):
                os.environ[key] = value


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def predict(client, state, cache_dir):
    # The full state, pinned model, and provider implementation invalidate the cache.
    contract = {'state': state, 'model': client.model,
                'provider_sha256': PROVIDER_HASH}
    encoded = json.dumps(contract, sort_keys=True, allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    path = cache_dir / (digest + '.json')
    if path.exists():
        answer = json.loads(path.read_text())
        if answer.get('request_hash') != digest:
            raise ValueError('cache hash mismatch')
        p = answer.get('probability')
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
            raise ValueError('invalid cached probability')
        if not isinstance(answer.get('model'), str) or not answer['model'].strip():
            raise ValueError('missing cached model')
        confidence = answer.get('confidence')
        if confidence is not None and (type(confidence) not in (int, float) or not 0 <= confidence <= 1):
            raise ValueError('invalid cached confidence')
        return answer, True
    answer = dict(client.evaluate(state), request_hash=digest)
    cache_dir.mkdir(parents=True, exist_ok=True)
    import threading
    temporary = path.with_suffix(f'.{threading.get_ident()}.tmp')
    write_json(temporary, answer)
    temporary.replace(path)
    return answer, False


def eligible_events(events, frames, start, end):
    """Require complete past and 24h follow-up, using availability only, never returns."""
    dates = {pair: set(frame['date']) for pair, frame in frames.items()}
    output = []
    for event in events:
        if not start <= event.date < end - pd.Timedelta(hours=24):
            continue
        required = pd.date_range(event.date - pd.Timedelta(minutes=15 * 120),
                                 event.date + pd.Timedelta(hours=24), freq='15min')
        if all(date in dates[event.pair] for date in required):
            output.append(event)
    return output


def independent_outcome(event, frames, funding, options):
    # A unit trial with ample equity avoids minimum notional and portfolio competition.
    end = event.date + pd.Timedelta(hours=24, minutes=15)
    frame = clip_frame(frames[event.pair], event.date, end)
    result = simulate_portfolio({event.pair: frame}, [event],
                                replace(options, initial_equity=10000),
                                start=event.date, end=end,
                                funding_frames={event.pair: funding[event.pair]})
    if len(result.trades) != 1:
        raise ValueError('independent event did not produce exactly one trade')
    return result.trades[0]


def run(args):
    start, split, end = [pd.Timestamp(x, tz='UTC') for x in (args.start, args.split, args.end)]
    if not start < split < end or not 0 < args.threshold < 1 or args.limit_per_split < 0:
        raise ValueError('require ordered dates, 0<threshold<1 and nonnegative sample limit')
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError('output-dir must be empty; caches are stored separately')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    params = TrendCompressionParameters() if args.signal_mode == 'dense' else DualMaParameters(pyramid_enabled=False)
    strategy = {'signal_mode': args.signal_mode, 'parameters': asdict(params)}
    # Frozen before labels are read: identical exits/risk/costs for baseline and Jev.
    options = BacktestOptions(exit_mode='fixed3', time_mode='24h', pyramid_enabled=False)
    manifest = {'status': 'running', 'strategy': strategy, 'options': asdict(options),
                'model': args.model, 'threshold': args.threshold,
                'start': str(start), 'split': str(split), 'end_exclusive': str(end),
                'limit_per_split': args.limit_per_split, 'sampling': 'uniform chronological, outcome-blind',
                'live_claim_allowed': False, 'snapshot_sha256': fingerprint(args.snapshot),
                'code_sha256': {str(p.relative_to(ROOT)): fingerprint(p) for p in [
                    Path(__file__).resolve(), ROOT/'user_data/strategy_lib/jev_provider.py',
                    ROOT/'user_data/strategy_lib/jev_research.py',
                    ROOT/'user_data/strategy_lib/v2_backtester.py',
                    ROOT/'user_data/strategy_lib/trend_compression_history.py',
                    ROOT/'user_data/strategy_lib/dual_ma_signal_engine.py']}}
    write_json(args.output_dir/'manifest.json', manifest)
    load_key_file(args.env_file)
    client = JevClient(model=args.model, timeout=30)
    instruments, categories = load_snapshot(args.snapshot)
    pairs = available_trade_pairs(args.data_dir, instruments, args.pairs)
    warmup = start - pd.Timedelta(days=180)
    frames, funding, sources = {}, {}, {}
    for pair in pairs:
        inst = instruments[pair]
        frames[pair] = {tf: clip_frame(load_frame(args.data_dir, inst, tf), warmup, end)
                        for tf in ['15m', '1h', '4h']}
        frames[pair]['1d'] = resample_confirmed(frames[pair]['15m'], '1d')
        funding[pair] = load_funding(args.data_dir, inst)
        sources[pair] = {tf: fingerprint(args.data_dir/f'{inst}-{tf}.feather') for tf in ['15m', '1h', '4h']}
        fp = args.data_dir/f'{inst}-funding.feather'
        sources[pair]['funding'] = fingerprint(fp) if fp.exists() else None
    manifest['sources'] = sources
    base = {pair: frames[pair]['15m'] for pair in pairs}
    print(f'Loaded {len(pairs)} pairs; generating {args.signal_mode} signals', flush=True)
    if args.signal_mode == 'dense':
        def scan(pair):
            f = frames[pair]
            return build_pair_events(pair, categories[pair], f['15m'], f['4h'], f['1d'], params)
        with ThreadPoolExecutor(max_workers=4) as pool:
            events = rank_events([e for group in pool.map(scan, pairs) for e in group])
    else:
        universe = pd.read_feather(args.universe)
        manifest['universe_sha256'] = fingerprint(args.universe)
        btc = clip_frame(load_frame(args.data_dir, instruments['BTC/USDT:USDT'], '4h'), warmup, end)
        eth = clip_frame(load_frame(args.data_dir, instruments['ETH/USDT:USDT'], '4h'), warmup, end)
        events = []
        for pair in pairs:
            events.extend(build_events(pair, categories[pair], frames, btc, eth, universe, params, 'dual_ma'))
    manifest['generated_events'] = len([e for e in events if start <= e.date < end])
    write_json(args.output_dir/'manifest.json', manifest)
    all_rows, results = [], {}
    for name, begin, finish in [('early', start, split), ('late', split, end)]:
        eligible = eligible_events(events, base, begin, finish)
        selected = select_events(eligible, args.limit_per_split)
        print(f'{name}: {len(eligible)} eligible, {len(selected)} sampled', flush=True)
        predictions, cache_hits = {}, 0
        # Finish all predictions before attaching any outcomes.
        def assess(event):
            state = build_state(event, frames[event.pair], options, strategy)
            return event_key(event), predict(client, state, args.cache_dir)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, (key, (answer, hit)) in enumerate(pool.map(assess, selected)):
                predictions[key] = answer
                cache_hits += int(hit)
                if (i + 1) % 20 == 0 or i + 1 == len(selected):
                    print(f'{name}: Jev {i+1}/{len(selected)} (cache {cache_hits})', flush=True)
                    write_json(args.output_dir/f'{name}-predictions.json', predictions)
                    manifest['prediction_progress'] = {'split': name, 'completed': i+1, 'total': len(selected)}
                    write_json(args.output_dir/'manifest.json', manifest)
        write_json(args.output_dir/f'{name}-predictions.json', predictions)
        rows = []
        for event in selected:
            answer = predictions[event_key(event)]
            trade = independent_outcome(event, base, funding, options)
            rows.append({'split': name, 'event_key': event_key(event), 'pair': event.pair,
                         'date': str(event.date), 'side': event.side,
                         'probability': answer['probability'], 'model': answer['model'],
                         'accepted': answer['probability'] >= args.threshold,
                         'net_pnl': trade.net_pnl, 'won': int(trade.net_pnl > 0),
                         'exit_reason': trade.exit_reason, 'imputed_funding': trade.imputed_funding})
        accepted = [e for e in selected if predictions[event_key(e)]['probability'] >= args.threshold]
        stats = {'eligible_events': len(eligible), 'sampled_events': len(selected),
                 'accepted_events': len(accepted), 'cache_hits': cache_hits,
                 'calibration': calibration([r['probability'] for r in rows], [r['won'] for r in rows]),
                 'accepted_calibration': calibration([r['probability'] for r in rows if r['accepted']],
                                                     [r['won'] for r in rows if r['accepted']])}
        for label, entries in [('baseline', selected), ('jev', accepted)]:
            portfolio = simulate_portfolio(base, entries, options, start=begin, end=finish, funding_frames=funding)
            stress = simulate_portfolio(base, entries, replace(options, fee_rate=.001, slippage_rate=.001,
                                        missing_funding_rate_per_8h=.0002), start=begin, end=finish, funding_frames=funding)
            stats[label] = summarize_backtest(portfolio)
            if not portfolio.trades:
                stats[label]['win_rate'] = None
                stats[label]['pf'] = None
            stats[label]['stress_pf'] = summarize_backtest(stress)['pf'] if stress.trades else None
            pd.DataFrame([asdict(t) for t in portfolio.trades]).to_csv(args.output_dir/f'{name}-{label}-trades.csv', index=False)
            portfolio.equity_curve.to_csv(args.output_dir/f'{name}-{label}-equity.csv', index=False)
        results[name] = stats
        all_rows.extend(rows)
        pd.DataFrame(all_rows).to_csv(args.output_dir/'predictions-and-outcomes.csv', index=False)
        write_json(args.output_dir/'metrics.json', results)
    manifest.update(status='completed_research_only', provider_models=sorted({r['model'] for r in all_rows}))
    write_json(args.output_dir/'manifest.json', manifest)
    write_report(args.output_dir, manifest, results)
    print(json.dumps(results, default=str, ensure_ascii=False), flush=True)


def write_report(directory, manifest, results):
    lines = ['# Jev 均线开仓历史评估', '',
             f"策略模式：`{manifest['strategy']['signal_mode']}`；模型：`{manifest['model']}`；固定筛选阈值：{manifest['threshold']:.0%}。", '',
             f"期间：{manifest['start']} 至 {manifest['end_exclusive']}（不含）；分界：{manifest['split']}。", '',
             '成功定义为独立交易扣手续费、滑点与资金费后净利润 > 0。Jev 概率不是已证实胜率。', '',
             '## 同一信号样本的组合回测', '',
             '| 时间段 | 筛选 | 成交数 | 胜率 | PF | 最大回撤 | 期末权益（初始100） | 双倍成本PF |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    def num(x):
        return '—' if x is None else f'{x:.3f}'
    for name, stats in results.items():
        for label in ['baseline', 'jev']:
            m = stats[label]
            lines.append(f"| {name} | {label} | {m['trades']} | {num(m['win_rate'])} | {num(m['pf'])} | {m['drawdown']:.3f} | {m.get('final_equity', 0):.2f} | {num(m['stress_pf'])} |")
    lines += ['', '## 独立信号预测校验', '']
    for name, stats in results.items():
        lines += [f"- {name}：可用信号 {stats['eligible_events']}，均匀抽样 {stats['sampled_events']}，Jev 接受 {stats['accepted_events']}。",
                  f"  全样本校准：`{json.dumps(stats['calibration'], ensure_ascii=False)}`",
                  f"  筛选后校准：`{json.dumps(stats['accepted_calibration'], ensure_ascii=False)}`"]
    lines += ['', '## 固定规则与证据边界', '',
              '- dense 复用项目日线/4H EMA20/60同向、15m MA/EMA20/60/120跨度≤2 ATR进入密集时开仓的历史引擎；这是旧版单根收拢规则，不等同于观察台的持续缠绕筛选。dual_ma 可选复用1H EMA20/60、15m首次回踩。',
              '- 入场为信号后下一根15m开盘；原策略止损，3R止盈，入场满24h所在15m K线收盘退出（最长24h15m），不加仓；两组使用同一固定退出规则。3倍杠杆，常态单笔风险0.75%，组合风险2%，最多3仓、同向最多2仓。',
              '- 单边手续费0.05%、滑点0.05%；缺失资金费每8h按不利方向0.01%估算，压力测试加倍。',
              '- 概率校准按每信号独立交易统计；组合回测包含资金/仓位限制，因此成交数与独立信号数不同。',
              '- 沿用原引擎先排除下一根开盘越过止损的不可执行候选；这是执行资格过滤，非纯信号总体。按时间等距抽样，只评估抽中的信号，不代表全量策略；--limit-per-split 0 可评估全部。为保证24h标签完整，每段最后24h不新增信号，缺K样本剔除。',
              '- 参数与阈值在取标签前固定，后半段只做时间切分验证；历史区间与现有币种队列已被旧研究使用，存在存活偏差与模型历史记忆风险，不称为全新样本外。',
              '- 仅给模型已收盘历史特征，隐去交易对与绝对日期；请求缓存按完整输入/模型/适配器代码哈希，API失败即中断，不用假概率补齐。',
              '- 本次不生成实盘配置；交易数不足或没有筛选后交易时无法证明有效提升。',
              '', '接口依据：[TypeSafe 官方文档](https://docs.typesafe.ai/introduction)。', '']
    (directory/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, default=Path('user_data/data/okx_v2'))
    p.add_argument('--snapshot', type=Path, default=Path('config/okx_v2_universe.json'))
    p.add_argument('--universe', type=Path, default=Path('user_data/data/okx_v2/universe_top30.feather'))
    p.add_argument('--signal-mode', choices=['dense', 'dual_ma'], default='dense')
    p.add_argument('--start', default='2026-06-11')
    p.add_argument('--split', default='2026-07-11')
    p.add_argument('--end', default='2026-08-10')
    p.add_argument('--pairs', nargs='+')
    p.add_argument('--limit-per-split', type=int, default=300)
    p.add_argument('--threshold', type=float, default=.6)
    p.add_argument('--model', default='jev-1.13.0')
    p.add_argument('--env-file', type=Path)
    p.add_argument('--cache-dir', type=Path, default=Path('user_data/backtest_results/jev-cache'))
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    try:
        run(args)
    except Exception as exc:
        # Do not expose HTTP bodies, headers or credentials in errors.
        marker = args.output_dir/'manifest.json'
        if marker.exists():
            manifest = json.loads(marker.read_text())
            if manifest.get('status') == 'running':
                manifest.update(status='failed', error_type=type(exc).__name__)
                if isinstance(exc, JevProviderError):
                    manifest['provider_error'] = exc.reason
                write_json(marker, manifest)
        reason = exc.reason if isinstance(exc, JevProviderError) else type(exc).__name__
        print(f'Research failed: {reason}', file=sys.stderr)
        raise SystemExit(1)

if __name__ == '__main__':
    main()
