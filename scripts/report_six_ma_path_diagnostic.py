#!/usr/bin/env python3
"""Build the Chinese six-MA direction-versus-execution diagnostic report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import pandas as pd

try:
    from scripts.report_okx_v2 import render_equity_png
except ModuleNotFoundError:  # Direct execution adds scripts/, not repo root.
    from report_okx_v2 import render_equity_png


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path-dir",
        type=Path,
        default=Path("user_data/backtest_results/okx-sixma-paths-recent6m"),
    )
    parser.add_argument(
        "--execution-dir",
        type=Path,
        default=Path("user_data/backtest_results/okx-sixma-execution-recent6m-final"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/okx-sixma-path-diagnostic"),
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    path_summary = pd.read_csv(args.path_dir / "path-summary.csv").set_index(
        "trigger"
    )
    portfolio = pd.read_csv(
        args.path_dir / "portfolio-comparison.csv"
    ).set_index("trigger")
    matrix = pd.read_csv(args.execution_dir / "execution-matrix.csv")
    forward = json.loads(
        (args.execution_dir / "forward-candidate.json").read_text(encoding="utf-8")
    )
    path_manifest = json.loads(
        (args.path_dir / "run-manifest.json").read_text(encoding="utf-8")
    )
    execution_manifest = json.loads(
        (args.execution_dir / "run-manifest.json").read_text(encoding="utf-8")
    )
    expected_paths = (args.path_dir / "pullback_rejection-paths.csv").resolve()
    actual_paths = Path(execution_manifest["paths"]).resolve()
    if path_manifest.get("purpose") != "six_ma_direction_vs_execution_diagnostic":
        raise SystemExit("unexpected path diagnostic purpose")
    if execution_manifest.get("purpose") != "post_holdout_execution_diagnostic":
        raise SystemExit("unexpected execution diagnostic purpose")
    if (
        path_manifest.get("start") != execution_manifest.get("start")
        or path_manifest.get("end") != execution_manifest.get("end")
        or actual_paths != expected_paths
    ):
        raise SystemExit("path and execution diagnostics do not describe the same run")
    if forward.get("status") != "diagnostic_only_requires_fresh_forward_data":
        raise SystemExit("forward candidate must remain diagnostic-only")
    monthly = pd.read_csv(args.execution_dir / "forward-candidate-monthly.csv")
    trades = pd.read_csv(args.execution_dir / "forward-candidate-trades.csv")
    equity = pd.read_csv(args.execution_dir / "forward-candidate-equity.csv")

    for source, destination in (
        (args.path_dir / "path-summary.csv", "path-summary.csv"),
        (args.path_dir / "portfolio-comparison.csv", "trigger-portfolio-comparison.csv"),
        (args.path_dir / "compression_close-side-summary.csv", "compression-close-side-summary.csv"),
        (args.path_dir / "nested_breakout-side-summary.csv", "nested-breakout-side-summary.csv"),
        (args.path_dir / "pullback_rejection-side-summary.csv", "pullback-rejection-side-summary.csv"),
        (args.path_dir / "pullback_rejection-pair-summary.csv", "pullback-rejection-pair-summary.csv"),
        (args.path_dir / "run-manifest.json", "path-run-manifest.json"),
        (args.execution_dir / "execution-matrix.csv", "execution-matrix.csv"),
        (args.execution_dir / "forward-candidate.json", "forward-candidate.json"),
        (args.execution_dir / "forward-candidate-monthly.csv", "forward-candidate-monthly.csv"),
        (args.execution_dir / "forward-candidate-trades.csv", "forward-candidate-trades.csv"),
        (args.execution_dir / "forward-candidate-equity.csv", "forward-candidate-equity.csv"),
        (args.execution_dir / "run-manifest.json", "execution-run-manifest.json"),
    ):
        shutil.copyfile(source, args.output_dir / destination)
    render_equity_png(
        args.output_dir / "forward-candidate-equity.png",
        equity,
        "OKX Six-MA Short Forward Candidate - Diagnostic Equity",
    )

    metrics = forward["metrics"]
    stress = forward["stress_metrics"]
    practical = matrix.loc[
        (matrix["direction"] == "short")
        & (matrix["stop_multiplier"] == 1.0)
        & (matrix["exit_mode"] == "hybrid")
        & (matrix["time_mode"] == "24h")
    ].iloc[0]
    if (
        int(practical["trades"]) != int(metrics["trades"])
        or abs(float(practical["pf"]) - float(metrics["pf"])) > 1e-9
    ):
        raise SystemExit("forward candidate does not match the execution matrix")
    unconstrained = matrix.iloc[0]
    month_profitable = int((monthly["final_equity"] > 100).sum())
    month_total = len(monthly)
    requested_lines = []
    for pair in ("BEAT/USDT:USDT", "BLEND/USDT:USDT", "XRP/USDT:USDT"):
        selected = trades.loc[trades["pair"] == pair]
        requested_lines.append(
            f"- {pair.split('/')[0]}：{len(selected)} 笔，净收益 "
            f"{float(selected['net_pnl'].sum()) if not selected.empty else 0.0:.2f} USDT"
        )

    direct = path_summary.loc["compression_close"]
    nested = path_summary.loc["nested_breakout"]
    pullback = path_summary.loc["pullback_rejection"]
    direct_portfolio = portfolio.loc["compression_close"]
    nested_portfolio = portfolio.loc["nested_breakout"]
    pullback_portfolio = portfolio.loc["pullback_rejection"]
    short_side = pd.read_csv(
        args.path_dir / "pullback_rejection-side-summary.csv"
    ).set_index("side").loc["short"]

    report = rf"""# 六均线方向与执行诊断报告

## 结论

**你的观察有一部分被数据支持：六均线信号并非完全没有方向价值，当前主要问题确实包含入场和止损执行；但证据还不足以直接实盘。**

15 分钟六线密集后直接入场的 {int(direct['complete_24h_events'])} 个完整信号中，{pct(float(direct['stopped_then_3r_rate']))} 在触发原止损后，24 小时内又从原入场价走到顺向 3R。这说明当前密集区止损会把一部分后来方向正确的交易提前洗掉。

三种固定高周期条件的执行对比：

| 15m 触发 | 完整路径 | 24h方向正确率 | 止损前到2R（含未止损） | 止损后又到3R | 组合PF | 双倍成本PF |
|---|---:|---:|---:|---:|---:|---:|
| 密集收盘直接入场 | {int(direct['complete_24h_events'])} | {pct(float(direct['positive_24h_rate']))} | {pct(float(direct['target_2r_before_stop_rate']))} | {pct(float(direct['stopped_then_3r_rate']))} | {float(direct_portfolio['pf']):.3f} | {float(direct_portfolio['stress_pf']):.3f} |
| 再次突破 | {int(nested['complete_24h_events'])} | {pct(float(nested['positive_24h_rate']))} | {pct(float(nested['target_2r_before_stop_rate']))} | {pct(float(nested['stopped_then_3r_rate']))} | {float(nested_portfolio['pf']):.3f} | {float(nested_portfolio['stress_pf']):.3f} |
| 第一次回踩并收回 | {int(pullback['complete_24h_events'])} | {pct(float(pullback['positive_24h_rate']))} | {pct(float(pullback['target_2r_before_stop_rate']))} | {pct(float(pullback['stopped_then_3r_rate']))} | {float(pullback_portfolio['pf']):.3f} | {float(pullback_portfolio['stress_pf']):.3f} |

直接入场频率最高，但假突破和扫损过多；第一次回踩的整体组合最接近盈亏平衡，因此选它继续拆分多空。

## 空单方向发现

第一次回踩空单共有 {int(short_side['events'])} 个完整路径：

- 6 小时方向正确率：{pct(float(short_side['positive_6h_rate']))}
- 24 小时方向正确率：{pct(float(short_side['positive_24h_rate']))}
- 24 小时平均方向收益：{float(short_side['mean_return_24h_r']):.3f}R
- 在原止损前达到 2R（含 24h 内未止损）：{pct(float(short_side['target_2r_before_stop_rate']))}
- 在原止损前达到 3R（含 24h 内未止损）：{pct(float(short_side['target_3r_before_stop_rate']))}
- 24 小时触发原止损：{pct(float(short_side['stop_rate_24h']))}

这组数据说明空单值得进入新的前向观察，但样本仍小，而且它是在查看同一半年数据后发现的。

## 推荐的前向观察候选

这不是实盘配置，只是下一段新数据要冻结观察的版本：

- 方向：只做空
- 4H：六线密集后向下突破；不强制完整空头排列
- 4H 密集区最长保留 12 根 4H K 线；突破后最长等待 48 根 15m K 线
- 加密合约要求 BTC/ETH 至少一个同向，且两者均不能明确反向；BTC/ETH 只作参考，不交易
- 15m：第一次回踩六线带并收回后确认，下一根开盘执行
- 止损：当前六线密集区上侧 + 0.2 ATR，不额外放宽
- 退出：2R 平 40%，剩余保本/EMA20 跟踪；最长 24 小时
- 取消原来的 6 小时无进展强制退出
- 3x，单笔风险 0.75%，组合开放风险 2%，最多 3 仓且同向最多 2 仓

在已经查看过的半年数据上，该候选为：

- 交易：{int(metrics['trades'])} 笔
- 胜率：{pct(float(metrics['win_rate']))}
- PF：{float(metrics['pf']):.3f}
- 双倍成本 PF：{float(stress['pf']):.3f}
- 最大回撤：{pct(float(metrics['drawdown']))}
- 最大连续亏损：{int(metrics['max_consecutive_losses'])}
- 平均持仓：{float(metrics['average_holding_hours']):.2f} 小时
- 100U → {float(metrics['final_equity']):.2f}U；双倍成本后 → {float(stress['final_equity']):.2f}U
- 各月独立以 100U 重置统计，{month_profitable}/{month_total} 个区间终值高于 100U；部分月份只有 1–4 笔，稳定性仍不足

## 为什么不选回测终值最高版本

矩阵最高项是 `{unconstrained['direction']}`、止损 `{float(unconstrained['stop_multiplier']):.1f}x`、`{unconstrained['exit_mode']}/{unconstrained['time_mode']}`：{int(unconstrained['trades'])} 笔、PF {float(unconstrained['pf']):.3f}，但平均持仓 {float(unconstrained['average_holding_hours']):.2f} 小时。它样本更小且已经不属于偏短线，因此不作为推荐候选。

## 用户指定合约

{chr(10).join(requested_lines)}
- KO：上市不足 30 天，仍按既定规则排除。

## 证据边界

- 当前半年已经被用于发现并选择“只做空 + 24h”方案，因此上述 PF 不是新的样本外成绩。
- 现存合约快照仍有幸存者偏差；未完整模拟 mark price、维护保证金档位和每个合约的实际下单规格。
- 下一步必须冻结该候选，用未来 60–90 天新数据记录；中途不能再根据表现改参数。
- 在新前向样本达到足够交易数并通过 PF、压力成本、回撤门槛前，不生成自动实盘配置。

## 复现命令

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/analyze_six_ma_signal_paths.py \
  --start 2026-02-10 --end 2026-08-11 \
  --output-dir user_data/backtest_results/okx-sixma-paths-recent6m

docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/analyze_six_ma_execution_variants.py \
  --paths user_data/backtest_results/okx-sixma-paths-recent6m/pullback_rejection-paths.csv \
  --start 2026-02-10 --end 2026-08-11 \
  --output-dir user_data/backtest_results/okx-sixma-execution-recent6m-final

python3 \
  scripts/report_six_ma_path_diagnostic.py \
  --path-dir user_data/backtest_results/okx-sixma-paths-recent6m \
  --execution-dir user_data/backtest_results/okx-sixma-execution-recent6m-final \
  --output-dir reports/okx-sixma-path-diagnostic
```
"""
    (args.output_dir / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    print(f"generated {args.output_dir / 'FINAL_REPORT.md'}")


if __name__ == "__main__":
    main()
