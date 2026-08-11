# 项目进度交接

更新时间：2026-08-11

> **OKX V2 / 双均线最新交接请先读 `PROJECT_CONTEXT.md`。**
> 下文保留 Binance V1 历史结论；OKX 工作以 PROJECT_CONTEXT 为准。

## Binance V1（历史）

- Docker Desktop 与 Freqtrade 2026.5.1 环境已验证。
- V1 六均线 + 回踩已完成研究；**2026 留出未通过，不应实盘**。
- 详见 `reports/FINAL_REPORT.md`。

## OKX 短线（当前）

- 数据：全市场 1H 404 + 高频研究队列 118 已齐。
- 压缩/突破 V2：正式研究失败（交易过稀）。
- 双均线+滚仓：正式 32 候选失败（交易够、PF≈0.74 不够）。
- **当前证据下不可实盘。** 下一棒应提 PF，不放宽硬门槛。
- 详见：`PROJECT_CONTEXT.md`

## 常用命令

```bash
docker compose config --quiet
docker compose run --rm --no-deps --entrypoint python freqtrade -m unittest discover -s tests -v

# OKX 双均线研究
docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/run_okx_v2_research.py --signal-mode dual_ma \
  --output-dir user_data/backtest_results/okx-v2-dualma-full --signal-workers 4
```

## 关键文件

- 交接：`PROJECT_CONTEXT.md`
- OKX 设计/计划：`docs/superpowers/specs|plans/2026-08-10-okx-shortline-v2*`
- 双均线引擎：`user_data/strategy_lib/dual_ma_signal_engine.py`
- 研究入口：`scripts/run_okx_v2_research.py`
- 最新失败产物：`user_data/backtest_results/okx-v2-dualma-full/`
