# 六均线方向与执行诊断报告

## 结论

**你的观察有一部分被数据支持：六均线信号并非完全没有方向价值，当前主要问题确实包含入场和止损执行；但证据还不足以直接实盘。**

15 分钟六线密集后直接入场的 438 个完整信号中，31.74% 在触发原止损后，24 小时内又从原入场价走到顺向 3R。这说明当前密集区止损会把一部分后来方向正确的交易提前洗掉。

三种固定高周期条件的执行对比：

| 15m 触发 | 完整路径 | 24h方向正确率 | 止损前到2R（含未止损） | 止损后又到3R | 组合PF | 双倍成本PF |
|---|---:|---:|---:|---:|---:|---:|
| 密集收盘直接入场 | 438 | 46.58% | 27.63% | 31.74% | 0.543 | 0.390 |
| 再次突破 | 323 | 41.49% | 29.72% | 6.81% | 0.828 | 0.567 |
| 第一次回踩并收回 | 138 | 47.10% | 36.23% | 11.59% | 0.962 | 0.703 |

直接入场频率最高，但假突破和扫损过多；第一次回踩的整体组合最接近盈亏平衡，因此选它继续拆分多空。

## 空单方向发现

第一次回踩空单共有 49 个完整路径：

- 6 小时方向正确率：57.14%
- 24 小时方向正确率：57.14%
- 24 小时平均方向收益：0.169R
- 在原止损前达到 2R（含 24h 内未止损）：42.86%
- 在原止损前达到 3R（含 24h 内未止损）：22.45%
- 24 小时触发原止损：53.06%

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

- 交易：42 笔
- 胜率：50.00%
- PF：1.501
- 双倍成本 PF：1.280
- 最大回撤：4.76%
- 最大连续亏损：6
- 平均持仓：9.90 小时
- 100U → 107.41U；双倍成本后 → 104.11U
- 各月独立以 100U 重置统计，6/7 个区间终值高于 100U；部分月份只有 1–4 笔，稳定性仍不足

## 为什么不选回测终值最高版本

矩阵最高项是 `short`、止损 `2.0x`、`fixed5/none`：25 笔、PF 2.080，但平均持仓 186.67 小时。它样本更小且已经不属于偏短线，因此不作为推荐候选。

## 用户指定合约

- BEAT：0 笔，净收益 0.00 USDT
- BLEND：0 笔，净收益 0.00 USDT
- XRP：2 笔，净收益 -0.05 USDT
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

/Users/syndred/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  scripts/report_six_ma_path_diagnostic.py \
  --path-dir user_data/backtest_results/okx-sixma-paths-recent6m \
  --execution-dir user_data/backtest_results/okx-sixma-execution-recent6m-final \
  --output-dir reports/okx-sixma-path-diagnostic
```
