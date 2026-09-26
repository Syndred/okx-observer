# Jev 均线策略历史评估

## 入口

本研究独立运行，不改观察台扫描、不下单。复用 JevPlay 的 TypeSafe System One 协议及本项目的信号/组合回测器。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-research.txt
# 环境已有 TYPESAFE_API_KEY 或 JEV_API_KEY 时无需 --env-file。
.venv/bin/python scripts/run_jev_ma_research.py \
  --env-file /本地/JevPlay/.env.development \
  --signal-mode dense \
  --start 2026-06-11 --split 2026-07-11 --end 2026-08-10 \
  --limit-per-split 300 --threshold 0.6 \
  --output-dir user_data/backtest_results/jev-dense-new-run
```

输出目录必须为空；缓存独立保留在 `user_data/backtest_results/jev-cache/`，失败重跑请指定新输出目录并复用相同缓存路径。不在命令行传密钥，不将 `.env` 提交。

## 规则与比较口径

- `dense`：复用 `trend_compression_history`，日线和4H EMA20/60趋势同向，15m MA/EMA20/60/120跨度不大于2 ATR，进入资格状态时开仓。**这是旧研究的单根密集规则，不是实时观察台的持续缠绕/多次交叉判定。**
- `dual_ma`：可选另一条已有路径，1H EMA20/60同向、15m首次回踩；不把它误称为密集开仓。
- 统一执行：下一15m开盘，信号原始止损，固定3R止盈；入场满24h那根15m K线收盘退出，最长24h15m。不开滚仓，避免改变仓位后混淆概率标签。该退出规则是本次固定评估协议，不是沿用旧研究最优组合。
- 原策略与Jev组共用同一候选样本、费用、资金费、滑点和风险约束，仅Jev组过滤预测概率低于阈值的信号。
- 独立标签按每个信号单独模拟，净利润大于0算成功。组合回测另含仓位竞争、资金与回撤风控，因此实际成交数不等于独立信号数。
- 手续费/滑点各为单边0.05%；缺失资金费按每8h不利0.01%估算；压力测试全部加倍。
- 模型输入只使用决策时刻已收盘K线，删去交易对名和绝对日期，价格与量归一化；模型概率和真实胜率分开统计。

## 产物

- `REPORT.md`：中文比较与边界。
- `manifest.json`：状态、冻结参数、数据和代码哈希、模型版本、进度/失败原因。
- `metrics.json`：两个时间段的组合收益/胜率/PF/回撤、独立信号Brier/log loss/Wilson区间和分箱。
- `predictions-and-outcomes.csv`：逐信号预测与净盈利标签。
- `early/late-predictions.json`：预测回执；`*-trades.csv`、`*-equity.csv`：组合明细。

## 如何理解结果

默认每段按时间等距抽300个信号；`--limit-per-split 0`评估全量。抽样回测不能冒充全量结果。原引擎先按下一开盘是否跨过止损筛去不可执行候选；本研究比较的是同一可执行候选母集。缺K和段末不足完整持有期的信号排除。

现有历史与币种队列被旧研究用过，后半段仅为固定阈值的时间切分验证，不能称为全新样本外。币种存活偏差、模型对历史市场的潜在记忆、15m OHLC内无法恢复精确价格路径仍存在。没有成交时胜率/PF为空，API失败不补假概率。不得仅凭胜率高或少数盈利交易宣布策略有效。

接口：[TypeSafe 官方文档](https://docs.typesafe.ai/introduction)。Python SDK要求较新Python，本项目本地Python3.9用已有requests按同一协议调用；密钥仅留后端内存。
