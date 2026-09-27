# 固定长历史均值回归信号族、EMA9/21、1R目标与30分钟持仓协议

冻结日期：2026-09-27；在第五轮训练失败归因后、下一轮JeV评分及开发标签计算前冻结。

## 改动与依据

第五轮趋势回调EMA20/60候选训练只有81笔挂单、47笔成交，基线常规胜率40.43%、PF0.503、均值-0.162%；39笔成交持有至30分钟上限，且评分阈值都未达样本及盈亏门槛。失败原因是训练样本不足且策略质量不合格。

随后只统计候选信号数量，没有计算收益、评分概率或开发标签。训练窗口的现有 `range_reversion` 规则在EMA9/21和ATR下限0.004下生成157笔挂单；ATR下限0.003下生成522笔。下一轮固定采用0.003以检验更充分的训练样本。结果未测；不据订单数量声称盈利。

## 固定设置

- 信号为现有 `scan_profit(family='range_reversion', fast=9, slow=21, volume_multiple=1, stop_buffer_atr=0.3, cooldown_bars=12)`：15/60分钟趋势偏向弱时，5分钟收盘相对EMA21偏离超过1个ATR后，以方向相反的确认K线尝试回归。
- 使用 `CostAwareParameters(family='range_reversion', fast=9, slow=21, stop_atr=3, min_atr_pct=0.003, min_body_atr=0)`。结构止损与ATR3止损取更远一侧。
- 被动限价偏移0.25 ATR，止盈1R，最长持仓30分钟。
- 数据、八币、UTC训练/开发区间、Jev模型、费用、资金费、限价成交与同K路径假设、验收阈值和前轮相同；阈值固定为[0.15,0.25,0.35,0.45,0.55,0.65]。不反转概率、不增删阈值，不读取预留价格。

## 固定验收

训练常规及两种压力情景每种至少80笔成交；常规净胜率≥50%、PF≥1.2、均值>0；两种压力PF≥1、均值≥0。训练失败即停止A/B。训练通过后冻结阈值；开发A/B各情景至少30笔且满足同一门槛，A+B常规至少100笔，A失败则停止B。所有标签通过后才验证组合资金约束、回撤和执行敏感性。全程不下实盘订单。

训练结果写入新的空目录，保留此前失败产物；记录代码、协议、依赖、数据哈希及真实API/缓存计数。训练不合格时不得读取开发标签。

## 复现命令

```powershell
.\.venv\Scripts\python.exe scripts/run_jev_passive_filter.py `
  --long-history-range-reversion-1r-hold-30m `
  --data-dir user_data/data/okx_scalp_long `
  --env-file .env.jev `
  --output-dir user_data/backtest_results/jev-passive-long-desktop-20260927-range-reversion-ema9-21-atr003-target1r-hold30m
```
