# 固定长历史均值回归、强实体确认、0.5 ATR限价与1R目标协议

冻结日期：2026-09-27；在第七轮Jev训练失败及训练段方向性归因后、任何下一轮Jev评分或开发标签计算前冻结。

## 改动与依据

第七轮均值回归EMA9/21、ATR下限0.003、0.5 ATR限价偏移、1R目标、30分钟上限产生522笔挂单、196笔常规成交。Jev阈值0.15筛选后184笔，但常规PF0.877、均值-0.032%；成本/严格压力PF为0.526/0.507。阈值0.25仅12笔成交，样本不足。长短方向的成本压力均未盈利。

训练段离线基线归因显示，给方向反转K线增加实体确认可略微改善结果并保留样本：`min_body_atr=0.2`时常规172笔成交、PF0.854、均值-0.037%；`min_body_atr=0.5`时130笔成交、胜率51.54%、PF0.895、均值-0.026%。0.5实体确认的两种压力PF为0.535/0.518，仍明显不合格，因此本轮继续通过固定阈值筛选训练结果，未假设可盈利。

## 固定设置

- 信号固定为现有 `scan_profit(family='range_reversion', fast=9, slow=21, volume_multiple=1, stop_buffer_atr=0.3, cooldown_bars=12)`，仅保留与信号方向一致、实体至少0.5 ATR的反转K线。
- 固定 `CostAwareParameters(family='range_reversion', fast=9, slow=21, stop_atr=3, min_atr_pct=0.003, min_body_atr=0.5)`。结构止损与ATR3止损取更远一侧。
- 被动限价偏移0.5 ATR、止盈1R、最长持仓30分钟。
- 数据、八币、UTC训练/开发区间、Jev模型、费用、资金费、成交路径假设和训练/压力/开发门槛不变。Jev阈值固定为[0.15,0.25,0.35,0.45,0.55,0.65]，不反转概率、不增删阈值，不读取预留价格。

## 固定验收

训练常规及两种压力情景每种至少80笔成交；常规净胜率≥50%、PF≥1.2、均值>0；两种压力PF≥1、均值≥0。训练失败即停止A/B。训练通过后冻结阈值；开发A/B各情景至少30笔且满足同一门槛，A+B常规至少100笔，A失败则停止B。所有标签通过后才验证组合资金约束、回撤和执行敏感性。全程不下实盘订单。

训练产物写入全新空目录并记录代码、协议、依赖、数据哈希及真实API/缓存计数。训练不合格时不得读取开发标签。

## 复现命令

```powershell
.\.venv\Scripts\python.exe scripts/run_jev_passive_filter.py `
  --long-history-range-reversion-offset05-body05-1r-hold-30m `
  --data-dir user_data/data/okx_scalp_long `
  --env-file .env.jev `
  --output-dir user_data/backtest_results/jev-passive-long-desktop-20260927-range-reversion-offset05-body05-target1r-hold30m
```
