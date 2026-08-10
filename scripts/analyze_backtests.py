#!/usr/bin/env python3
"""Turn exported Freqtrade result archives into auditable CSV and equity curves."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile


def load_strategy_result(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        result_name = next(
            name
            for name in archive.namelist()
            if name.endswith(".json") and not name.endswith("_config.json")
        )
        payload = json.loads(archive.read(result_name))
    strategy_name = next(iter(payload["strategy"]))
    return payload["strategy"][strategy_name]


def summarize_strategy(result: dict, scenario: str) -> dict:
    wins = [trade["profit_abs"] for trade in result["trades"] if trade["profit_abs"] > 0]
    losses = [trade["profit_abs"] for trade in result["trades"] if trade["profit_abs"] < 0]
    average_win = sum(wins) / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = average_win / abs(average_loss) if average_loss else 0.0
    starting = float(result["starting_balance"])
    final = float(result["final_balance"])
    curve = equity_curve(result)
    balances = [starting, *(balance for _, balance in curve)]
    return {
        "scenario": scenario,
        "start": result.get("backtest_start", ""),
        "end": result.get("backtest_end", ""),
        "starting_balance": round(starting, 6),
        "final_balance": round(final, 6),
        "return_pct": round((final / starting - 1) * 100, 6),
        "total_trades": result["total_trades"],
        "long_trades": result.get("trade_count_long", 0),
        "short_trades": result.get("trade_count_short", 0),
        "wins": result["wins"],
        "losses": result["losses"],
        "winrate_pct": round(float(result["winrate"]) * 100, 6),
        "average_win_usdt": round(average_win, 6),
        "average_loss_usdt": round(average_loss, 6),
        "payoff_ratio": round(payoff, 6),
        "profit_factor": round(float(result["profit_factor"]), 6),
        "max_drawdown_pct": round(float(result["max_drawdown_account"]) * 100, 6),
        "max_consecutive_losses": result["max_consecutive_losses"],
        "minimum_balance": round(min(balances), 6),
        "reached_1000": max(balances) >= 1000,
        "reached_10000": max(balances) >= 10000,
    }


def equity_curve(result: dict) -> list[tuple[int, float]]:
    balance = float(result["starting_balance"])
    points: list[tuple[int, float]] = []
    for trade in sorted(result["trades"], key=lambda item: item["close_timestamp"]):
        balance += float(trade["profit_abs"])
        points.append((int(trade["close_timestamp"]), balance))
    return points


def discover_latest(root: Path) -> list[tuple[str, Path]]:
    grouped: dict[str, list[Path]] = {}
    for path in root.rglob("*.zip"):
        grouped.setdefault(path.parent.name, []).append(path)
    return [
        (scenario, max(paths, key=lambda item: item.stat().st_mtime))
        for scenario, paths in sorted(grouped.items())
    ]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("No backtest rows found")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_equity_csv(path: Path, results: list[tuple[str, dict]]) -> None:
    rows = []
    for scenario, result in results:
        for timestamp, balance in equity_curve(result):
            rows.append(
                {
                    "scenario": scenario,
                    "timestamp": datetime.fromtimestamp(
                        timestamp / 1000, tz=timezone.utc
                    ).isoformat(),
                    "balance_usdt": round(balance, 6),
                }
            )
    write_csv(path, rows)


def write_pair_csv(path: Path, results: list[tuple[str, dict]]) -> None:
    rows = []
    for scenario, result in results:
        for pair in result.get("results_per_pair", []):
            if pair.get("key") == "TOTAL":
                continue
            rows.append(
                {
                    "scenario": scenario,
                    "pair": pair.get("key", ""),
                    "trades": pair.get("trades", 0),
                    "profit_usdt": round(float(pair.get("profit_total_abs", 0)), 6),
                    "profit_pct": round(float(pair.get("profit_total", 0)) * 100, 6),
                    "wins": pair.get("wins", 0),
                    "losses": pair.get("losses", 0),
                }
            )
    write_csv(path, rows)


def render_equity_png(path: Path, results: list[tuple[str, dict]]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1600, 900
    margin = (110, 70, 60, 100)
    image = Image.new("RGB", (width, height), "#08111f")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=16)
    all_points = [point for _, result in results for point in equity_curve(result)]
    if not all_points:
        raise ValueError("No trades available for equity curve")
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    all_balances = [float(result["starting_balance"]) for _, result in results]
    all_balances += [point[1] for point in all_points]
    min_y, max_y = min(all_balances), max(all_balances)
    if max_x == min_x:
        max_x += 1
    if max_y == min_y:
        max_y += 1
    left, top, right, bottom = margin
    plot_w = width - left - right
    plot_h = height - top - bottom
    for index in range(6):
        y = top + index * plot_h / 5
        value = max_y - index * (max_y - min_y) / 5
        draw.line((left, y, left + plot_w, y), fill="#223047", width=1)
        draw.text((15, y - 10), f"{value:,.0f} U", fill="#9fb0c8", font=small)
    colors = ["#4fd1c5", "#f6ad55", "#63b3ed", "#fc8181", "#b794f4", "#68d391"]
    for index, (scenario, result) in enumerate(results):
        raw = equity_curve(result)
        if not raw:
            continue
        points = [
            (
                left + (timestamp - min_x) / (max_x - min_x) * plot_w,
                top + (max_y - balance) / (max_y - min_y) * plot_h,
            )
            for timestamp, balance in raw
        ]
        color = colors[index % len(colors)]
        draw.line(points, fill=color, width=3, joint="curve")
        legend_y = top + 8 + index * 30
        draw.line((left + 20, legend_y + 9, left + 65, legend_y + 9), fill=color, width=4)
        draw.text((left + 75, legend_y), scenario, fill="#e8eef7", font=small)
    draw.rectangle((left, top, left + plot_w, top + plot_h), outline="#718096", width=2)
    draw.text((left, 18), "MA/EMA V1 - Equity Curve", fill="#f7fafc", font=font)
    draw.text((left, height - 55), "Trade close time (UTC)", fill="#9fb0c8", font=small)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    archives = discover_latest(args.root)
    results = [(scenario, load_strategy_result(path)) for scenario, path in archives]
    rows = [summarize_strategy(result, scenario) for scenario, result in results]
    write_csv(args.output_prefix.with_suffix(".csv"), rows)
    write_equity_csv(args.output_prefix.with_name(args.output_prefix.name + "-equity.csv"), results)
    write_pair_csv(args.output_prefix.with_name(args.output_prefix.name + "-pairs.csv"), results)
    render_equity_png(args.output_prefix.with_name(args.output_prefix.name + "-equity.png"), results)


if __name__ == "__main__":
    main()
