# Crypto Trading System

本项目使用 Freqtrade + Docker 回测“六均线压缩 → 突破 → 第一次回踩”趋势系统。当前阶段只实现 V1：SMA20/60/120 与 EMA20/60/120，不加入 ADX、成交量或多周期过滤。

## 当前结论

V1 已完成本地部署、参数搜索、风险矩阵和独立留出测试。冻结方案在 2026 留出数据上 100U → 60.22U，PF 0.823，未通过稳健性验证，暂不建议实盘。完整结果见 `reports/FINAL_REPORT.md`，执行交接见 `PROJECT_PROGRESS.md`。

## 快速检查

```bash
./scripts/ft.sh --version
docker compose config --quiet
```

## 研究口径

- Binance USDT-M 永续合约，1h，逐仓，多空均可
- 初始资金 100 USDT
- 3x / 5x / 10x 杠杆
- 每笔风险 0.5% / 1% / 2% / 5%
- 3R / 5R / 8R / 10R 固定止盈
- 训练：2021–2024；验证：2025；最终留出：2026 年截至 8 月 10 日

完整定义见 `docs/superpowers/specs/2026-08-10-ma-ema-v1-backtest-design.md`。

## 主要产物

- `user_data/strategies/MA_EMA_Trend_Strategy.py`：Freqtrade 多空策略。
- `user_data/strategies/MA_EMA_Trend_Strategy.json`：冻结研究参数。
- `reports/final-results-equity.png`：最终资金曲线。
- `reports/capital-matrix.csv`：3x/5x/10x 与 1%/2%/5% 风险比较。
- `reports/data-availability.csv`：19 个合约真实数据范围。
