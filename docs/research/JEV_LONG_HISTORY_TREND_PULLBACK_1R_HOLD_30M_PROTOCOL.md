# 固定长历史趋势回调信号族、1R目标与30分钟持仓协议

冻结日期：2026-09-27；上一轮突破回踩训练只有2个订单，归因后、任何新Jev评分与开发标签计算前冻结。

## 改动与动机

上一轮EMA20/60压缩突破后回踩确认的 `retest` 信号族在三个月训练窗仅产生2个挂单、1笔成交，达不到各情景80笔的样本门槛。下一轮只把信号族切换到现有 `trend_pullback`，保留1R目标与30分钟上限。趋势回调可在趋势内价格回到EMA20附近并重新向趋势方向收盘时入场，不必等待压缩突破及之后的回踩序列；预期信号更多，但结果未测。

## 固定设置

- 数据、八币、UTC训练/开发区间、Jev模型、概率请求、费用、资金费、限价成交和验收阈值与上一轮协议相同；不读取预留价格。
- 固定 `CostAwareParameters(family='trend_pullback', stop_atr=3, min_atr_pct=0.004, min_body_atr=0)`。
- 信号使用已有5分钟EMA20/60与15/60分钟EMA9/21同向趋势：多头在趋势向上时回踩EMA20并收回其上方、阳线且收盘接近K线高位；空头对称。沿用12根K线冷却。
- `scan_cost_aware` 沿用趋势回调结构止损，并取结构止损与ATR3止损中更远一侧；最小波动比率0.004、不设额外实体下限。被动限价偏移0.25ATR、止盈1R、最长持仓30分钟。
- 除信号族从 `retest` 换为 `trend_pullback` 外，费用、穿价/跳空/同K路径假设和其余参数不变。Jev固定阈值[0.15,0.25,0.35,0.45,0.55,0.65]，不反转概率、不增删阈值。

## 固定验收

训练常规和两种压力情景各至少80笔；常规净胜率≥50%、PF≥1.2、均值>0；两压力PF≥1、均值≥0。训练失败即停止A/B。训练通过后冻结阈值；开发A/B各情景至少30笔且满足相同门槛，A+B常规至少100笔，A失败停止B。所有标签通过后才验证组合资金约束、回撤和执行敏感性，再考虑未触碰的预留数据。全程不下实盘订单。

复现命令：

```powershell
.\.venv\Scripts\python.exe scripts/run_jev_passive_filter.py `
  --long-history-trend-pullback-1r-hold-30m `
  --data-dir user_data/data/okx_scalp_long `
  --env-file .env.jev `
  --output-dir user_data/backtest_results/jev-passive-long-desktop-20260927-trend-pullback-target1r-hold30m
```

结果写入全新空目录；保留此前失败轮次并记录提交、依赖、数据哈希及API/缓存计数。训练不合格时不触碰开发标签。
