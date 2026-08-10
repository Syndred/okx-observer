# 项目进度交接

更新时间：2026-08-10

## 已完成

- Docker Desktop 与 Freqtrade 2026.5.1 环境已验证。
- 已创建 Binance USDT-M futures 配置，100 USDT 初始资金、逐仓、多空双向。
- 已实现 V1 六均线策略和无未来数据的第一次回踩状态机。
- 已实现密集区另一侧动态止损、固定 3R/5R/8R/10R、风险定额仓位和 3x/5x/10x。
- 已从 Binance 官方归档导入 19 个合约的 1H K 线、mark price 和 funding rate，共 57 个 Feather 文件。
- 已完成 80 轮训练参数搜索、前五候选 2025 验证、9 组风险矩阵和一次冻结后的 2026 留出测试。
- 已生成最终资金曲线、币种贡献、完整指标和中文报告。
- 21 个自动化测试全部通过；Freqtrade 策略发现状态为 OK，无辅助模块加载警告。

## 冻结研究参数

`compression=0.015`、`breakout=0.005`、`pullback_max_candles=3`、`pullback_tolerance=0.005`、`RR=5`。

研究基准风险：3x、每笔账户风险 1%、最多同时 3 仓。

## 当前结论

V1 未通过 2026 留出验证，不应进入实盘。详见 `reports/FINAL_REPORT.md`。

## 常用命令

```bash
# 环境与策略检查
docker compose config --quiet
./scripts/ft.sh --version
./scripts/ft.sh list-strategies --config /freqtrade/user_data/configs/config.base.json

# 全量测试
docker compose run --rm --no-deps --entrypoint python freqtrade -m unittest discover -s tests -v

# 重新导入官方历史数据
./scripts/download_data.sh

# 重新运行风险矩阵
./scripts/run_capital_matrix.sh 20210101-20260101 /freqtrade/user_data/backtest_results/capital-matrix

# 重新生成矩阵汇总
/Users/syndred/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  scripts/analyze_backtests.py \
  --root user_data/backtest_results/capital-matrix \
  --output-prefix reports/capital-matrix
```

## 文件位置

- 策略：`user_data/strategies/MA_EMA_Trend_Strategy.py`
- 最终参数：`user_data/strategies/MA_EMA_Trend_Strategy.json`
- 纯信号引擎：`user_data/strategy_lib/v1_signal_engine.py`
- 风险模型：`user_data/strategy_lib/risk_model.py`
- 配置：`user_data/configs/config.base.json`
- 原始回测：`user_data/backtest_results/`
- 参数搜索原始结果：`user_data/hyperopt_results/`
- 最终报告：`reports/FINAL_REPORT.md`

## 后续建议

若继续，下一阶段只实现 4H 趋势过滤 V2，并重新划分滚动窗口。不要根据已经看过的 2026 币种盈亏直接删币，也不要同时加入 ADX、成交量和斜率，否则无法判断哪个条件产生改进。
