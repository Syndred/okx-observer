# 项目进度交接

更新时间：2026-09-26

> **OKX V2 / 双均线最新交接请先读 `PROJECT_CONTEXT.md`。**
> 下文保留 Binance V1 历史结论；OKX 工作以 PROJECT_CONTEXT 为准。

## Jev 均线研究（2026-09-26）

- 已复用 JevPlay 的 System One 接口，密钥仅由环境变量或显式本地 env 文件读取。
- 新入口：`scripts/run_jev_ma_research.py`；默认日线/4H双EMA趋势 + 15m六线密集，`dual_ma` 可选旧双EMA回踩路径。
- 退出协议固定为3R、原止损、24h时间K收盘（最长24h15m）；Jev概率阈值固定0.60。不是旧滚仓最优参数重跑。
- 模型输入仅已收盘历史；概率预测与真实净盈利标签分开；缓存按请求/模型/代码哈希，失败不降级为假结果。
- 已通过74项相关测试；真实API已连通。600次真实预测与回测已完成：独立信号成功167/600（27.83%）；两段组合胜率25.35%/27.03%，PF0.637/0.613。Jev固定0.60阈值无成交，不能证明提升。报告：`reports/jev-ma-20260926/FINAL_REPORT.md`。
- 说明：`docs/research/JEV_MA_RESEARCH.md`。研究不改观察台、不下单。

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
