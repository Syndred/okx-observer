#!/usr/bin/env python3
"""Generate the final Chinese OKX V2 feasibility artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import pandas as pd


def threshold_crossings(
    equity: pd.DataFrame, thresholds: tuple[float, ...]
) -> dict[float, str]:
    result = {}
    for threshold in thresholds:
        matches = equity.loc[equity["equity"] >= threshold, "date"]
        result[threshold] = (
            pd.Timestamp(matches.iloc[0]).isoformat() if not matches.empty else "never"
        )
    return result


def money(value: float) -> str:
    return f"{value:,.2f} USDT"


def percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_equity_png(path: Path, equity: pd.DataFrame) -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1600, 700
    left, top, right, bottom = 110, 70, 60, 90
    image = Image.new("RGB", (width, height), "#08111f")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=16)
    plot_width, plot_height = width - left - right, height - top - bottom
    if equity.empty:
        draw.text((left, top), "No pseudo-holdout trades", fill="#e8eef7", font=font)
        image.save(path)
        return
    dates = pd.to_datetime(equity["date"], utc=True)
    x_values = dates.map(lambda value: value.timestamp()).astype(float)
    y_values = equity["equity"].astype(float)
    min_x, max_x = float(x_values.min()), float(x_values.max())
    min_y, max_y = float(y_values.min()), float(y_values.max())
    if min_x == max_x:
        max_x += 1
    if min_y == max_y:
        max_y += 1
    for index in range(6):
        y = top + index * plot_height / 5
        value = max_y - index * (max_y - min_y) / 5
        draw.line((left, y, left + plot_width, y), fill="#223047", width=1)
        draw.text((15, y - 10), f"{value:,.1f} U", fill="#9fb0c8", font=small)
    points = [
        (
            left + (x - min_x) / (max_x - min_x) * plot_width,
            top + (max_y - y) / (max_y - min_y) * plot_height,
        )
        for x, y in zip(x_values, y_values)
    ]
    if len(points) > 1:
        draw.line(points, fill="#4fd1c5", width=3, joint="curve")
    else:
        x, y = points[0]
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#4fd1c5")
    draw.rectangle((left, top, left + plot_width, top + plot_height), outline="#718096", width=2)
    draw.text((left, 18), "OKX Shortline V2 - Pseudo-holdout Equity", fill="#f7fafc", font=font)
    draw.text((left, height - 50), "UTC time", fill="#9fb0c8", font=small)
    image.save(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-dir", type=Path, default=Path("user_data/backtest_results/okx-v2"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/okx-v2"))
    parser.add_argument("--hourly-report", type=Path, default=Path("reports/okx-v2/hourly-availability.csv"))
    parser.add_argument("--data-report", type=Path, default=Path("reports/okx-v2/data-availability.csv"))
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    frozen = json.loads((args.research_dir / "frozen-candidate.json").read_text())
    holdout_payload = json.loads((args.research_dir / "pseudo-holdout-metrics.json").read_text())
    metrics = holdout_payload["metrics"]
    trades_path = args.research_dir / "pseudo-holdout-trades.csv"
    trades = pd.read_csv(trades_path) if trades_path.stat().st_size else pd.DataFrame()
    equity = pd.read_csv(args.research_dir / "pseudo-holdout-equity.csv")
    variants = pd.read_csv(args.research_dir / "pseudo-holdout-variants.csv")
    candidates = pd.read_csv(args.research_dir / "candidate-results.csv")
    pd.DataFrame([{"scope": "pseudo_holdout_default", **metrics}]).to_csv(
        output / "metrics.csv", index=False
    )
    equity.to_csv(output / "equity.csv", index=False)
    variants.to_csv(output / "option-comparison.csv", index=False)
    candidates.to_csv(output / "candidate-results.csv", index=False)
    trades.to_csv(output / "trades.csv", index=False)
    if not trades.empty:
        trades.groupby("pair", as_index=False).agg(
            trades=("net_pnl", "size"),
            net_pnl=("net_pnl", "sum"),
            average_r=("r_multiple", "mean"),
            fees=("fees", "sum"),
            funding=("funding", "sum"),
        ).sort_values("net_pnl", ascending=False).to_csv(output / "pair-contribution.csv", index=False)
        trades.groupby("category", as_index=False).agg(
            trades=("net_pnl", "size"),
            net_pnl=("net_pnl", "sum"),
            average_r=("r_multiple", "mean"),
        ).sort_values("net_pnl", ascending=False).to_csv(output / "category-contribution.csv", index=False)
        side = trades.groupby("side").agg(trades=("net_pnl", "size"), net_pnl=("net_pnl", "sum"))
    else:
        pd.DataFrame(columns=["pair", "trades", "net_pnl", "average_r", "fees", "funding"]).to_csv(
            output / "pair-contribution.csv", index=False
        )
        pd.DataFrame(columns=["category", "trades", "net_pnl", "average_r"]).to_csv(
            output / "category-contribution.csv", index=False
        )
        side = pd.DataFrame(columns=["trades", "net_pnl"])
    render_equity_png(output / "equity.png", equity)
    crossings = threshold_crossings(equity, (1_000, 10_000)) if not equity.empty else {1000: "never", 10000: "never"}
    selection_passed = frozen["status"] == "passed"
    holdout_passed = (
        float(metrics.get("pf") or 0) >= 1.15
        and float(metrics.get("drawdown") or 1) <= 0.35
        and float(metrics.get("final_equity") or 0) > 0
    )
    feasible = selection_passed and holdout_passed
    verdict = "有条件可行，仍必须先做前向模拟盘" if feasible else "当前证据下不可实盘"
    selection = frozen["selection_score"]
    params = frozen["parameters"]
    side_lines = []
    for name in ("long", "short"):
        if name in side.index:
            side_lines.append(
                f"- {name}: {int(side.loc[name, 'trades'])} 笔，净收益 {float(side.loc[name, 'net_pnl']):.2f} USDT"
            )
    if not side_lines:
        side_lines = ["- 无交易"]
    data_limit = ""
    if args.hourly_report.exists():
        availability = pd.read_csv(args.hourly_report)
        data_limit = (
            f"全市场小时筛选覆盖 {len(availability)} 个合约；最早数据 "
            f"{availability['first_candle'].dropna().min()}，最晚数据 "
            f"{availability['last_candle'].dropna().max()}。"
        )
    report = f"""# OKX U 本位永续短线系统 V2 最终报告

## 最终结论

**{verdict}。**

该结论按硬门槛得出，不按最高终值倒推参数。参数选择阶段状态为 `{frozen['status']}`；失败门槛为 `{selection.get('failed_gates', '') or '无'}`。如果门槛未全部通过，项目不会生成可直接实盘的配置。

## 冻结版本

- 4H：币种自身趋势；加密合约再叠加 BTC/ETH 宽松一致过滤。
- 1H：六均线 MA20/60/120 + EMA20/60/120，ATR 标准化压缩与突破。
- 15m：突破后首次回踩入场，最多等待 {params['pullback_wait_15m']} 根。
- 参数：compression `{params['compression_atr']}` ATR，breakout `{params['breakout_atr']}` ATR，pullback `{params['pullback_atr']}` ATR。
- 默认退出：2R 平 40%，剩余移保本并跟踪 EMA20，5R 强制退出，6/12/24 小时时间门槛。
- 风控：100 USDT 初始资金，逐仓 3x，最多 3 仓，同向最多 2 仓，总开放风险 2%。

## 参数选择阶段

- 样本外 Profit Factor：{selection.get('profit_factor')}
- 最大回撤：{percent(float(selection.get('max_drawdown') or 0))}
- 交易次数：{selection.get('trades')}
- 最差窗口 PF：{selection.get('worst_window_pf')}
- 双倍成本 PF：{selection.get('stress_pf')}

## 伪留出结果（冻结参数、默认退出）

- 交易次数：{int(metrics.get('trades') or 0)}
- 胜率：{percent(float(metrics.get('win_rate') or 0))}
- 平均盈利：{money(float(metrics.get('average_win') or 0))}
- 平均亏损：{money(float(metrics.get('average_loss') or 0))}
- 盈亏比：{float(metrics.get('payoff_ratio') or 0):.3f}
- Profit Factor：{float(metrics.get('pf') or 0):.3f}
- 最大回撤：{percent(float(metrics.get('drawdown') or 0))}
- 最大连续亏损：{int(metrics.get('max_consecutive_losses') or 0)}
- 初始 / 最终资金：{money(float(metrics.get('initial_equity') or 100))} / {money(float(metrics.get('final_equity') or 0))}
- 最低 / 最高资金：{money(float(metrics.get('min_equity') or 0))} / {money(float(metrics.get('max_equity') or 0))}
- 费用：{money(float(metrics.get('fees') or 0))}
- 净资金费（正数为支付）：{money(float(metrics.get('funding') or 0))}
- 首次达到 1,000 USDT：{crossings[1000]}
- 首次达到 10,000 USDT：{crossings[10000]}

## 多空拆分

{chr(10).join(side_lines)}

## 数据范围与限制

{data_limit}

- 只使用已确认 K 线；1H/4H 信息在收盘后才允许进入 15m 决策。
- 当前 OKX 合约快照存在“现存合约幸存者偏差”，无法恢复已下架合约的完整历史币池。
- 同根 K 线同时触发止损和止盈时按止损处理；普通成本假设为单边 0.05% 手续费 + 0.05% 滑点，压力测试翻倍。
- 伪留出不是未来模拟盘；任何“通过”仍需至少 60–90 天 OKX dry-run 才能进入小额实盘。
- KO-USDT-SWAP 在本次快照时上市不足 30 天且属于 OKX 股票类合约，因此被自动排除；BEAT、BLEND、XRP 是否进入交易由历史 Top30 每小时决定。

## 对 100 → 10,000 USDT 的回答

回测只回答这套规则在历史样本中的表现，不能证明未来能把 100 USDT 变成 10,000 USDT。只有资金曲线实际穿越对应门槛时才记录日期；未穿越则明确写 `never`。100 倍目标不作为参数评分项，避免为了终值接受不可生存的回撤。
"""
    (output / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    print(f"Generated {output / 'FINAL_REPORT.md'} verdict={verdict}")


if __name__ == "__main__":
    main()
