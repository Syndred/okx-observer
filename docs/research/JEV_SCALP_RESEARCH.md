# 5分钟均线超短线 / Jev 优化研究

## 固定实验设计

- 真正5m行情，不由15m插值：BTC、ETH、SOL、XRP、DOGE、ADA、LINK、AVAX的OKX USDT永续。
- 数据2026-07-28至09-26 UTC（不含末日）；前三天预热。训练07-31至08-27，验证08-27至09-11，最终留出09-11至09-26。
- 144组候选：EMA9/21或20/60、压缩后突破或回踩、均线间距≤0.5/1 ATR、止损1.5/2.5 ATR、止盈0.75/1/1.5R、持仓15/30/60min。
- 过去5根至少3根双EMA密集、最近12根存在密集区；已收盘15m EMA9/21提供方向。突破超过前6根高/低点0.05 ATR；回踩要求触碰快线且收盘收回。信号成交量至少为20根中位数；单币信号至少间隔60min。
- 开仓前预期毛止盈距离至少为往返费用/滑点的1.5倍，避免目标连成本都盖不住。信号形成后下一根5m开盘成交。
- 交易单边手续费与滑点分别0.05%；缺资金费按UTC00/08/16对既有仓位不利扣0.01%。压力测试三者加倍。资金费是保守估算，并非拉取实际历史资金费。
- 复用项目组合模拟器，3倍杠杆、单笔权益风险0.75%、组合风险2%、最多3仓、同向最多2仓；不滚仓。新增参数均可选，不改变原策略默认行为。

## 选择和达标

训练用独立交易标签快速筛选，前12个候选进入验证。优先选择同时满足样本数、**净盈利胜率>50%**、PF≥1.1和平均净收益>0的方案；没有通过者仍保存最佳诊断候选，明确标记未过门。

参数冻结后，Jev在验证期全量信号上预测。只在验证期从预设0.15至0.55的9个阈值选择；至少50个筛选后标签，同时要求净胜率>50%、PF≥1.1。若无合格阈值，保存最佳诊断阈值并标记失败。此后写出冻结文件，再处理最终留出期标签，不根据留出成绩重新优化。

最终采用真实组合回测：至少100笔、观察净胜率>50%、PF≥1.1、期末权益>100U、回撤≤20%、双倍成本PF≥1。此前验证也须过门。Jev必须覆盖全期信号才可以标记`jev_passed`；预算截断实验不算完整策略达标。**观察胜率>50%不等于统计上证明未来胜率必然>50%。**

## 运行

```bash
.venv/bin/pip install -r requirements-research-test.txt
.venv/bin/python scripts/download_okx_scalp_data.py \
  --start 2026-07-28 --end 2026-09-26
.venv/bin/python scripts/run_jev_scalp_research.py \
  --env-file /本地/JevPlay/.env.development \
  --output-dir user_data/backtest_results/jev-scalp-new-run
.venv/bin/python -m pytest tests/test_scalp* tests/test_run_jev_scalp_research.py \
  tests/test_download_okx_scalp_data.py -q
```

下载器按页断点续传，数据与请求缓存均留在Git忽略目录。输出目录必须为空；模型结果缓存独立保存于`user_data/backtest_results/jev-scalp-cache`，重跑可复用。

## 已知限制

- 独立标签的PF基于单位名义本金收益；最终组合PF基于实际盈亏金额，受资金和仓位限制，两者不能混称。
- OHLC无法还原5m内价格顺序，止损和止盈同根触碰时先按止损处理。止损/止盈的旧回测时间标签仍为bar起点，所以不能把0分钟标签误称为瞬时成交；超时退出按真实bar收盘记录。
- API真实推理延迟没有被映射成历史秒级成交滑点；最终结果仍是研究回测，需前向模拟验证实际执行。
- 固定8币池存在选择限制，不能外推全市场。公开数据依据：[OKX 官方 API](https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks-history)。
