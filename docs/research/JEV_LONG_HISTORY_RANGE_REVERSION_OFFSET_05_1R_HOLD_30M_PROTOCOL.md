# 固定长历史均值回归信号族、0.5 ATR限价、1R目标与30分钟持仓协议

冻结日期：2026-09-27；在第六轮训练失败归因及训练段执行敏感性检查后、下一轮Jev评分与开发标签计算前冻结。

## 改动与依据

第六轮均值回归候选（EMA9/21、ATR下限0.003、限价偏移0.25 ATR、1R/30分钟）有522笔训练挂单、329笔成交，基线常规PF0.750、均值-0.063%；Jev阈值0.15保留316笔常规成交但没有改善，阈值0.25只有27笔成交，仍低于80笔门槛。退出归因显示多数30分钟到期单接近盈亏平衡，普通压力下成本令结果转差。

训练段离线执行敏感性检查只比较限价偏移，没有新的Jev调用。0.5 ATR偏移保留522笔挂单、196笔常规成交，基线胜率50.51%、PF0.846、均值-0.040%；成本压力PF0.505、均值-0.160%，严格压力PF0.490、均值-0.169%。0.75 ATR只有110笔常规成交，PF0.730、均值-0.081%。0.5 ATR是这三个偏移中基线较好的一个，仍远未达到压力验收；本协议只检验Jev能否从0.5 ATR候选中筛出合格子集，不预设结果。

## 固定设置

- 信号固定为现有 `scan_profit(family='range_reversion', fast=9, slow=21, volume_multiple=1, stop_buffer_atr=0.3, cooldown_bars=12)`。
- 固定 `CostAwareParameters(family='range_reversion', fast=9, slow=21, stop_atr=3, min_atr_pct=0.003, min_body_atr=0)`。结构止损与ATR3止损取更远一侧。
- 唯一参数改动：限价偏移固定为0.5 ATR；其他成交与风控规则不变。止盈1R，最长持仓30分钟。
- 数据、八币、UTC训练/开发区间、Jev模型、费用、资金费、限价成交与同K路径假设、训练/压力/开发验收阈值均沿用上一轮。阈值固定为[0.15,0.25,0.35,0.45,0.55,0.65]；不反转概率、不增删阈值，不读取预留价格。

## 固定验收

训练常规及两种压力情景每种至少80笔成交；常规净胜率≥50%、PF≥1.2、均值>0；两种压力PF≥1、均值≥0。训练失败即停止A/B。训练通过后冻结阈值；开发A/B各情景至少30笔且满足同一门槛，A+B常规至少100笔，A失败则停止B。所有标签通过后才验证组合资金约束、回撤和执行敏感性。全程不下实盘订单。

训练结果写入新的空目录，保留此前失败产物；记录代码、协议、依赖、数据哈希及真实API/缓存计数。训练不合格时不得读取开发标签。

## 复现命令

```powershell
.\.venv\Scripts\python.exe scripts/run_jev_passive_filter.py `
  --long-history-range-reversion-1r-hold-30m-offset-05 `
  --data-dir user_data/data/okx_scalp_long `
  --env-file .env.jev `
  --output-dir user_data/backtest_results/jev-passive-long-desktop-20260927-range-reversion-offset05-target1r-hold30m
```
