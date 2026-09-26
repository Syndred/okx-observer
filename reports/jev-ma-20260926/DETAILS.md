# Jev 均线开仓历史评估

策略模式：`dense`；模型：`jev-1.13.0`；固定筛选阈值：60%。

期间：2026-06-11 00:00:00+00:00 至 2026-08-10 00:00:00+00:00（不含）；分界：2026-07-11 00:00:00+00:00。

成功定义为独立交易扣手续费、滑点与资金费后净利润 > 0。Jev 概率不是已证实胜率。

## 同一信号样本的组合回测

| 时间段 | 筛选 | 成交数 | 胜率 | PF | 最大回撤 | 期末权益（初始100） | 双倍成本PF |
|---|---|---:|---:|---:|---:|---:|---:|
| early | baseline | 142 | 0.254 | 0.637 | 0.232 | 77.38 | 0.468 |
| early | jev | 0 | — | — | 0.000 | 100.00 | — |
| late | baseline | 148 | 0.270 | 0.613 | 0.269 | 76.79 | 0.466 |
| late | jev | 0 | — | — | 0.000 | 100.00 | — |

## 独立信号预测校验

- early：可用信号 1650，均匀抽样 300，Jev 接受 0。
  全样本校准：`{"win_rate": 0.30333333333333334, "brier": 0.23306799999999994, "log_loss": 0.6878289011191809, "accuracy": 0.6966666666666667, "mean_probability": 0.19280000000000005, "wilson95": {"low": 0.2540711301237818, "high": 0.3575684385578729}, "n": 300, "calibration_bins": [{"lower": 0.0, "upper": 0.2, "n": 188, "mean_probability": 0.1302127659574468, "win_rate": 0.30851063829787234}, {"lower": 0.2, "upper": 0.4, "n": 107, "mean_probability": 0.2923364485981308, "win_rate": 0.2897196261682243}, {"lower": 0.4, "upper": 0.6, "n": 5, "mean_probability": 0.41600000000000004, "win_rate": 0.4}, {"lower": 0.6, "upper": 0.8, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.8, "upper": 1.0, "n": 0, "mean_probability": null, "win_rate": null}]}`
  筛选后校准：`{"win_rate": null, "brier": null, "log_loss": null, "accuracy": null, "mean_probability": null, "wilson95": null, "n": 0, "calibration_bins": [{"lower": 0.0, "upper": 0.2, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.2, "upper": 0.4, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.4, "upper": 0.6, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.6, "upper": 0.8, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.8, "upper": 1.0, "n": 0, "mean_probability": null, "win_rate": null}]}`
- late：可用信号 2118，均匀抽样 300，Jev 接受 0。
  全样本校准：`{"win_rate": 0.25333333333333335, "brier": 0.18854333333333329, "log_loss": 0.5695495596419439, "accuracy": 0.7466666666666667, "mean_probability": 0.18886666666666665, "wilson95": {"low": 0.20744975020452644, "high": 0.30545411559958296}, "n": 300, "calibration_bins": [{"lower": 0.0, "upper": 0.2, "n": 184, "mean_probability": 0.1309782608695652, "win_rate": 0.20108695652173914}, {"lower": 0.2, "upper": 0.4, "n": 113, "mean_probability": 0.2755752212389381, "win_rate": 0.3274336283185841}, {"lower": 0.4, "upper": 0.6, "n": 3, "mean_probability": 0.47333333333333333, "win_rate": 0.6666666666666666}, {"lower": 0.6, "upper": 0.8, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.8, "upper": 1.0, "n": 0, "mean_probability": null, "win_rate": null}]}`
  筛选后校准：`{"win_rate": null, "brier": null, "log_loss": null, "accuracy": null, "mean_probability": null, "wilson95": null, "n": 0, "calibration_bins": [{"lower": 0.0, "upper": 0.2, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.2, "upper": 0.4, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.4, "upper": 0.6, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.6, "upper": 0.8, "n": 0, "mean_probability": null, "win_rate": null}, {"lower": 0.8, "upper": 1.0, "n": 0, "mean_probability": null, "win_rate": null}]}`

## 固定规则与证据边界

- dense 复用项目日线/4H EMA20/60同向、15m MA/EMA20/60/120跨度≤2 ATR进入密集时开仓的历史引擎；这是旧版单根收拢规则，不等同于观察台的持续缠绕筛选。dual_ma 可选复用1H EMA20/60、15m首次回踩。
- 入场为信号后下一根15m开盘；原策略止损，3R止盈，入场满24h所在15m K线收盘退出（最长24h15m），不加仓；两组使用同一固定退出规则。3倍杠杆，常态单笔风险0.75%，组合风险2%，最多3仓、同向最多2仓。
- 单边手续费0.05%、滑点0.05%；缺失资金费每8h按不利方向0.01%估算，压力测试加倍。
- 概率校准按每信号独立交易统计；组合回测包含资金/仓位限制，因此成交数与独立信号数不同。
- 沿用原引擎先排除下一根开盘越过止损的不可执行候选；这是执行资格过滤，非纯信号总体。按时间等距抽样，只评估抽中的信号，不代表全量策略；--limit-per-split 0 可评估全部。为保证24h标签完整，每段最后24h不新增信号，缺K样本剔除。
- 参数与阈值在取标签前固定，后半段只做时间切分验证；历史区间与现有币种队列已被旧研究使用，存在存活偏差与模型历史记忆风险，不称为全新样本外。
- 仅给模型已收盘历史特征，隐去交易对与绝对日期；请求缓存按完整输入/模型/适配器代码哈希，API失败即中断，不用假概率补齐。
- 本次不生成实盘配置；交易数不足或没有筛选后交易时无法证明有效提升。

接口依据：[TypeSafe 官方文档](https://docs.typesafe.ai/introduction)。
