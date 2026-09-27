# Jev 长历史限价筛选：强实体均值回归实验报告

日期：2026-09-27（UTC）

仓库：`Syndred/okx-observer`，分支 `codex/desktop-jev-research`

代码与冻结协议提交：`1ac0bc3493c14bfca4a666d5ba33469b8b77d6fd`

## 结论

均值回归EMA9/21、ATR下限0.003、0.5 ATR实体确认、0.5 ATR限价偏移、1R目标和30分钟上限，在训练段产生318笔挂单。318次真实Jev调用、缓存0次。状态为 `no_qualified_training_threshold`；没有计算开发A/B、组合标签或预留价格，没有实盘订单。

## 环境与数据

本机 Python 3.12.10 虚拟环境，pandas 2.3.3、NumPy 2.5.3、pyarrow 24.0.0、requests 2.34.2、pytest 8.4.2；Jev模型 `jev-1.13.0`。长历史数据 `complete=true`，488,448根K线、5,088条资金费，清单 SHA-256 `4f388ac2ae520c0a23b7af18c7133cd6dbbaa8083e631cda7e636a981eaf942f`。数据包 SHA-256 `a1ecfb0fde2c21a713fa4cfa127cfa03228215e7a91899618a7920fd419a1e0e`。

## Jev训练结果

| 情景/阈值 | 成交 | 净胜率 | PF | 平均净收益 |
|---|---:|---:|---:|---:|
| 基线常规 | 130 | 51.54% | 0.895 | -0.026% |
| 基线成本压力 | 130 | 43.08% | 0.535 | -0.146% |
| 基线严格压力 | 125 | 42.40% | 0.518 | -0.156% |
| Jev阈值0.15常规 | 123 | 51.22% | 0.951 | -0.012% |
| Jev阈值0.25常规 | 18 | 50.00% | 0.545 | -0.149% |

阈值0.15在常规净胜率上达到50%，PF、收益均值和两种压力结果仍失败。阈值0.25样本仅18笔且净亏；0.35及以上没有成交。六个固定阈值均不合格。

基线成交中，15笔触及1R目标，15笔触及初始止损；100笔在30分钟到期退出，52胜，PF1.003，平均净收益约+0.001%。时间退出组接近盈亏平衡，费用压力使结果明显为负。

## 训练归因与当前状态

增加0.5 ATR反转实体过滤后，常规PF由上轮0.846升至0.895，常规均值亏损由-0.040%收窄至-0.026%；Jev阈值0.15后PF为0.951，仍未达到1.2。成本和严格压力PF分别只有0.569和0.551。强化实体确认带来有限改善，没有形成覆盖成本的优势。

另对现有 `sweep_reclaim` 规则做了训练段无Jev基线筛查：EMA9/21、ATR下限0.003、实体下限0.5 ATR、0.5 ATR限价偏移只有73笔常规成交，PF0.708；样本和收益都不合格。它没有进入Jev评分。此前的突破回踩信号也只有1笔训练成交；趋势回调、均值回归和基础突破候选均未达到门槛。

当前已测试的信号族与执行变体没有提供足以支持另一项固定候选的训练证据。项目没有合格策略；不得宣称盈利或读取开发/预留段。若继续研究，应先定义一套独立、可解释的新入场规则并冻结协议，再按同一训练门槛筛选。

## 产物

- 训练结果（Git忽略）：`user_data/backtest_results/jev-passive-long-desktop-20260927-range-reversion-offset05-body05-target1r-hold30m/`
- 运行日志（Git忽略）：`work/logs/run_jev_passive_long_desktop_20260927-range-reversion-offset05-body05.log`
- 运行前记录（Git忽略）：`work/logs/jev-passive-long-desktop-20260927-range-reversion-offset05-body05-preflight.json`
- 冻结协议：`docs/research/JEV_LONG_HISTORY_RANGE_REVERSION_OFFSET_05_BODY_05_1R_HOLD_30M_PROTOCOL.md`
