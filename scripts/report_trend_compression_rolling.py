#!/usr/bin/env python3
"""Build the Chinese report for trend-compression rolling-margin research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import pandas as pd

try:
    from scripts.report_okx_v2 import render_equity_png
except ModuleNotFoundError:
    from report_okx_v2 import render_equity_png


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--research-dir",
        type=Path,
        default=Path("user_data/backtest_results/okx-trend-compression-rolling"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/okx-trend-compression-rolling"),
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    matrix = pd.read_csv(args.research_dir / "selection-matrix.csv")
    result = json.loads((args.research_dir / "result.json").read_text(encoding="utf-8"))
    manifest = json.loads((args.research_dir / "run-manifest.json").read_text(encoding="utf-8"))
    trades = pd.read_csv(args.research_dir / "holdout-trades.csv")
    equity = pd.read_csv(args.research_dir / "holdout-equity.csv")
    if manifest.get("purpose") != "post_definition_pseudo_holdout":
        raise SystemExit("unexpected research purpose")
    if result.get("status") not in {"diagnostic_passed", "research_failed"}:
        raise SystemExit("unexpected research result status")
    for source, destination in (
        (args.research_dir / "selection-matrix.csv", "selection-matrix.csv"),
        (args.research_dir / "result.json", "result.json"),
        (args.research_dir / "run-manifest.json", "run-manifest.json"),
        (args.research_dir / "holdout-trades.csv", "holdout-trades.csv"),
        (args.research_dir / "holdout-equity.csv", "holdout-equity.csv"),
    ):
        shutil.copyfile(source, args.output_dir / destination)
    render_equity_png(
        args.output_dir / "holdout-equity.png",
        equity,
        "Daily + 4H Trend / 15m Compression - Rolling Margin Holdout",
    )

    frozen = result["frozen"]
    selection = result["selection_metrics"]
    holdout = result["holdout_metrics"]
    stress = result["holdout_stress_metrics"]
    fixed = result["fixed_100u_sizing_holdout_metrics"]
    exits = trades.groupby("exit_reason").agg(
        trades=("net_pnl", "size"), net=("net_pnl", "sum")
    )
    initial_stop = exits.loc["initial_stop"] if "initial_stop" in exits.index else {"trades": 0, "net": 0}
    target_rows = trades.loc[trades["exit_reason"].astype(str).str.startswith("margin_")]
    last_trade = pd.to_datetime(trades["close_date"], utc=True).max()
    top = matrix.head(5)
    top_lines = []
    for row in top.itertuples(index=False):
        top_lines.append(
            f"| {row.trend_mode} | {row.direction} | {row.collateral_fraction:.0%} | "
            f"{row.leverage:.0f}x | {row.account_profit_target:.0%} | {row.time_mode} | "
            f"{int(row.trades)} | {row.pf:.3f} | {row.stress_pf:.3f} | "
            f"{row.final_equity:.2f}U |"
        )
    report = f"""# 日线 + 4H 趋势 / 15m 六线密集 / 分仓滚动复利验证

## 最终结论

**当前版本未通过留出验证，不能用于实盘滚仓。**

你的资金使用方式已经按原意实现：每次不是全仓，而是按当时账户权益拿 30%或40%作保证金，使用3x或5x；平仓后下一笔按新权益重新计算。若密集区止损过远，仓位会自动缩小，单笔计划最坏损失不超过账户10%，组合开放风险不超过20%。

选择段看起来最好的版本是：

- 日线、4H 基础 EMA20/60 趋势同向；15m 六线跨度 ≤2 ATR
- 只做多；30%保证金；3x
- 满额使用30%保证金时，名义目标利润为当时账户的30%，等价于保证金 ROI 100%；若止损风险帽缩仓，实际目标金额同步降低
- 最长持仓72小时
- {int(selection['trades'])}笔，PF {float(selection['pf']):.3f}，100U → {float(selection['final_equity']):.2f}U

冻结后用于 2026-06-11 至 2026-08-11 的伪留出结果：

- {int(holdout['trades'])}笔，胜率 {float(holdout['win_rate']):.2%}
- PF {float(holdout['pf']):.3f}；双倍成本 PF {float(stress['pf']):.3f}
- 100U → {float(holdout['final_equity']):.2f}U；双倍成本后 → {float(stress['final_equity']):.2f}U
- 最大回撤 {float(holdout['drawdown']):.2%}，最大连续亏损 {int(holdout['max_consecutive_losses'])}笔
- 不把盈利继续放大仓位的100U封顶基准 → {float(fixed['final_equity']):.2f}U；复利版本亏损更大

## 为什么选择段翻倍，留出段却失败

- 留出期有 {int(initial_stop['trades'])} 笔初始止损，合计 {float(initial_stop['net']):.2f}U。
- 真正达到预设“赚20–30U再走”目标的只有 {len(target_rows)} 笔。
- 其余盈利主要来自72小时到期退出，无法覆盖连续止损。
- 回撤保护在 {last_trade.isoformat()} 前后停止继续开仓，因此后续月份没有用新仓把资金曲线救回来。
- 30%保证金、3x、账户30%盈利目标意味着价格约需顺向移动33.3%；对72小时短线而言目标偏远。

这证明分仓避免了单笔全仓爆掉，但滚动复利不会创造策略优势：信号顺风时增长更快，连续假压缩或趋势末端入场时也会更快缩小账户。

## 选择段前五名

| 趋势 | 方向 | 保证金 | 杠杆 | 账户目标 | 时间退出 | 交易 | PF | 压力PF | 终值 |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|
{chr(10).join(top_lines)}

选择段共有 {int(matrix['selection_passed'].sum())} / {len(matrix)} 组通过基础门槛，但冻结第一名在留出段失败。不能改选留出段中表现最好的一组来冒充新验证。

## 下一步应该怎么改

保持日线 + 4H + 15m 六线框架，但下一轮只改变入场确认，不再调保证金：

1. 日线和4H同向后，15m六线密集只进入观察，不立即开仓。
2. 等15m顺趋势突破密集区，或突破后的第一次回踩守住，再入场。
3. 固定30%保证金、3x；把账户盈利目标先降到10%–15%，避免要求三天内价格移动33%。
4. 新版本必须重新冻结选择段，再验证留出段；不过门仍不实盘。

## 证据边界

- 研究使用当前仍存续合约的历史数据，存在幸存者偏差。
- 资金费率已计入，但 mark price、维护保证金档位和每个合约实际最小下单规格仍是近似。
- 这段历史此前在其他策略研究中被查看过，因此称为伪留出，不是未来真实前向数据。
"""
    (args.output_dir / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    print(f"generated {args.output_dir / 'FINAL_REPORT.md'}")


if __name__ == "__main__":
    main()
