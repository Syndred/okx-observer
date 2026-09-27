# 项目进度交接

更新时间：2026-09-27

> **OKX V2 / 双均线最新交接请先读 `PROJECT_CONTEXT.md`。**
> 下文保留 Binance V1 历史结论；OKX 工作以 PROJECT_CONTEXT 为准。

## 台式机长历史研究结果（2026-09-27）

- 已从 GitHub 拉取最新 `main`：`079679e`，包含研究实现 `9263be9`；本轮在 `codex/desktop-jev-research` 分支继续。
- Windows 环境已装好 Python 3.12.10、pip、venv；`python`、`py -3.12` 命令已加入当前用户 PATH。项目 `.venv` 已基于该 Python 安装研究依赖。
- 数据包 SHA-256 与交接文档一致；包内 2,478 项清单全部校验通过。长历史 `complete=true`：488,448 根 K 线、5,088 条资金费，所有拼接输入及输出哈希、标准资金费结算检查通过；未读取预留价格。
- 五个定向测试文件共 81 项通过；Jev 配置可加载，模型为 `jev-1.13.0`。测试有 68 条 NumPy `Timedelta` 弃用警告。
- 首轮固定长历史训练结束状态为 `no_qualified_training_threshold`：233 条训练订单均真实调用 Jev（缓存 0）；六个阈值全部未通过。基线166笔成交，净胜率35.54%、PF0.509、均值-0.279%。未评估开发A/B、组合风控或预留；没有实盘订单，也没有盈利结论。
- 结果、训练门槛及亏损归因见 `reports/jev-passive-long-desktop-20260927/PROGRESS_REPORT.md`。归因显示首轮多数成交到60分钟仍未触及2R，已冻结唯一变更为1R目标，协议见 `docs/research/JEV_LONG_HISTORY_TARGET_1R_PROTOCOL.md`。
- 第二轮1R目标训练同样为 `no_qualified_training_threshold`：233条真实Jev调用、缓存0；基线166笔成交，胜率36.75%、PF0.490、均值-0.287%。开发A/B仍未评估。
- 第二轮亏损归因：32笔止盈、43笔初始止损、91笔60分钟到期；到期组胜率31.87%、PF0.288、均值-0.242%。下一轮假设是在1R目标下将持仓上限从60分钟改为30分钟；协议冻结前不启动。详见 `reports/jev-passive-long-desktop-20260927-target1r/PROGRESS_REPORT.md`。
- 第三轮1R/30分钟训练也为 `no_qualified_training_threshold`：233条真实Jev调用、缓存0；基线166笔成交，净胜率36.14%、PF0.458、均值-0.232%。133笔（80.1%）到30分钟退出，PF0.337、均值-0.222%；开发A/B仍未评估。
- 第三轮报告见 `reports/jev-passive-long-desktop-20260927-target1r-hold30m/PROGRESS_REPORT.md`。已推送1R/30分钟版本 `4970aff`，当时88项定向测试通过；A/B仍未评估。
- 第四轮突破回踩信号族训练仅2个挂单、1笔成交，真实Jev调用2次，样本门槛不可能达标；状态为 `no_qualified_training_threshold`。对应版本提交前112项定向测试通过。详见 `reports/jev-passive-long-desktop-20260927-retest-target1r-hold30m/PROGRESS_REPORT.md`。
- 第五轮5分钟EMA20/60趋势回调训练已完成，提交 `524c669` 已先行推送。81笔订单、47笔成交，真实Jev调用81次、缓存0；基线常规胜率40.43%、PF0.503、均值-0.162%，压力PF均约0.30。六个阈值全部未通过；未计算开发A/B和组合标签，预留价格未读。
- 第五轮归因：39笔30分钟到期交易中17胜，PF0.675、均值-0.060%；另有6笔初始止损、2笔1R止盈。信号基线不足80笔成交，筛选后的数量更少。训练期只数信号、不算收益的探索中，现有 `range_reversion` EMA9/21、ATR下限0.004产生157笔订单；将下限固定为0.003时产生522笔订单。下一轮据此冻结均值回归信号族、9/21 EMA和0.003 ATR下限，费用/成交、1R/30分钟、冷却12根及所有门槛不变；先记录并推送新协议，再做Jev训练评分。报告见 `reports/jev-passive-long-desktop-20260927-trend-pullback-1r-hold30m/PROGRESS_REPORT.md`。
- 第六轮均值回归训练522笔订单，522次真实Jev调用、缓存0；基线329笔成交，常规胜率49.85%、PF0.750、均值-0.063%，成本/严格压力PF为0.430/0.405。阈值0.15筛选316笔成交仍亏损；0.25只有27笔，六个阈值全部失败，未计算开发A/B。
- 第六轮仅在训练段做了无Jev执行敏感性归因：0.5 ATR限价偏移的基线相对0.25 ATR改善（196笔成交，胜率50.51%、PF0.846、均值-0.040%），0.75 ATR更差；0.5R目标、15/20/60分钟持仓及1.5 ATR止损也更差。下一轮冻结唯一改动为0.5 ATR限价偏移，跑Jev训练筛选。报告见 `reports/jev-passive-long-desktop-20260927-range-reversion-ema9-21-atr003-target1r-hold30m/PROGRESS_REPORT.md`。
- 第七轮0.5 ATR限价偏移训练仍失败：522次真实Jev调用、缓存0；基线196笔成交，常规PF0.846、均值-0.040%，压力PF0.505/0.490。阈值0.15有184笔但压力PF仅0.526/0.507；阈值0.25仅12笔。训练归因后，最小实体0.5 ATR的离线基线为130笔、常规PF0.895，较0实体略有改善但压力PF0.535，仍未达标。下一轮冻结唯一改动为增加0.5 ATR实体确认，并在推送协议后训练评分。报告见 `reports/jev-passive-long-desktop-20260927-range-reversion-offset05-target1r-hold30m/PROGRESS_REPORT.md`。
- 第八轮强实体确认训练318笔订单，318次真实Jev调用、缓存0；基线130笔成交，常规胜率51.54%、PF0.895、均值-0.026%，压力PF0.535/0.518。阈值0.15有123笔、PF0.951但压力PF0.569/0.551；阈值0.25只有18笔。训练不合格，未进入A/B。
- 训练段无Jev筛查的 `sweep_reclaim` 也只有73笔成交且PF0.708。现有突破回踩、趋势回调、均值回归和基础突破候选均未通过固定训练门槛；目前没有被证实的盈利策略，也没有充分训练证据支持新的固定变体。报告见 `reports/jev-passive-long-desktop-20260927-range-reversion-offset05-body05-target1r-hold30m/PROGRESS_REPORT.md`。
- 用户要求持续更换短线策略直到严格达标；Codex自动化已改为每5分钟唤回本线程检查活动运行句柄，进程仍在时继续轮询，目标未达而当前没有运行时从最新报告续做。
- 新的1小时横截面动量训练失败：3,636笔、净胜率31.49%、PF0.303、均值-0.202%，双倍成本/延迟压力PF0.104/0.092。首个运行因误引用旧拆分常量而错误延伸到08-27，已标为无效；按协议正确重跑03-01至06-01训练窗后仍失败。错误运行数字未用于筛选。
- 冻结横截面反转协议后测试反向排名：训练3,636笔，净胜率32.01%、PF0.287、均值-0.204%，压力PF0.090/0.083，未进入开发A/B。
- 单币连续四根同向K线后反转确认候选：训练505笔，净胜率29.70%、PF0.315、均值-0.203%，压力PF0.114/0.085，未进入开发A/B。亏损归因中320/505笔因30分钟到期退出，该组PF0.141、均值-0.185%。下一轮先冻结并筛选5/10/15/30分钟持仓上限；训练未过仍不打开开发或预留。
- 冻结的时限扫描全部失败：5/10/15/30分钟训练PF为0.106/0.226/0.241/0.315，净胜率14.85%/23.17%/23.56%/29.70%；常规平均净收益均为负，压力PF全部远低于1。A/B与预留未计算。下一候选固定为BTC一小时方向过滤的横截面动量（BTC涨时只多最强币，BTC跌时只空最弱币），先冻结协议再跑训练。结果 `user_data/backtest_results/streak-flip-hold-sweep-20260927/`，协议提交 `b2a1305`、代码提交 `424b38f`。
- 完整结果、实验完整性说明、定向验证及本轮代码/协议提交见 `reports/strategy-iteration-20260927/PROGRESS_REPORT.md`。预留目录未读、无实盘订单、未发现合格策略。
- BTC一小时方向过滤横截面动量训练失败：1,818笔，净胜率33.44%、PF0.341、平均净收益-0.1945%；双倍成本/严格压力PF0.127/0.104。未计算开发A/B或组合，预留价格未读。结果在 `user_data/backtest_results/cross-sectional-btc-regime-20260927-train-only/`。
- EMA20/60/120强趋势回踩确认训练失败：1,009笔，净胜率34.39%、PF0.542、均值-0.1987%；双倍成本/严格压力PF0.305/0.269。开发A/B与预留未评分。结果在 `user_data/backtest_results/trend-pullback-20260927-train-only/`；协议 `docs/research/JEV_TREND_PULLBACK_PROTOCOL.md`，实现提交 `5a422c1`。
- 短线盈利目标仍进行中。每5分钟监督任务保持启用；下一轮须从这里恢复，先冻结与前述横截面、反转和趋势回踩不同的策略协议，再运行训练门槛，失败后继续换策略。

## 台式机迁移交接（2026-09-27）

- 用户要求停止Mac研究计算，迁往台式机；本次行情下载已终止并保留断点，未启动长历史Jev评分。
- 实现提交 `9263be9`：官方长资金费准备、长行情拼接、冻结长历史训练/A/B分段。81项定向测试通过。
- 资金费8币各636条、缺失0；长行情前缀仅部分完成，尚未拼接。无已证实盈利版本。
- 独立接手文档：`docs/research/DESKTOP_HANDOFF_20260927.md`，包含代码拉取、Windows/Linux环境、Jev配置、数据包哈希和断点续跑命令。未提交数据/密钥，数据单独迁移。

## Jev 扩展历史迭代（2026-09-27，目标进行中）

- 前缀行情下载已完成（原exec84002终止成功）；新数据已拼接与补资金费，`okx_scalp_extended` 共207360根K/2160资金费，缺K/标准结算缺失0，旧文件未改。
- 训练07-01至08-27固定候选59，253订单评分（192缓存+61新API），仍无合格阈值：0.35为54笔/62.96%（缺80样本），0.25为107笔/48.60%（缺50%胜率）。未进入A/B与预留。
- 101项相关测试通过；报告 `reports/jev-passive-extended-20260927/PROGRESS_REPORT.md`。`scripts/prepare_extended_training.py` 准备数据；原筛选入口加 `--extended-training --data-dir user_data/data/okx_scalp_extended`。
- 下一步先核验更长官方档案数据可用性，再冻结覆盖多行情的固定更长训练区间；不按刚好够数停止扩样，不放宽盈利和压力标准。不存在已证实盈利版本。

## Jev 限价筛选与历史扩样（2026-09-27，目标进行中）

- 完成208次真实Jev预测（固定限价候选59，所有订单先评分）。训练6阈值均失败：0.25为87成交/49.43%胜率，0.35为37成交/70.27%胜率；分别缺胜率或样本，未下调门槛。
- 未预测A/B、未开启预留，无实盘订单。86项相关测试通过，基线指标与上一轮一致。
- 入口 `scripts/run_jev_passive_filter.py`；报告 `reports/jev-passive-filter-20260927/PROGRESS_REPORT.md`。
- 下一轮扩样协议已冻结：固定原8币，补06-28至07-28，训练07-01至08-27；原候选/六阈值/原80与30及总100样本、50%胜率和压力门槛不变。新前缀数据目录 `user_data/data/okx_scalp_prefix_20260628`，下载是否完成需检查实际进程及manifest，不能凭目录存在判断；资金费与拼接校验仍待做。

## Jev 被动限价迭代（2026-09-27，目标进行中）

- 64组限价研究完成；候选59训练166成交PF1.423，但A50成交PF0.910、胜率44%，未达标。没有组合收益或实盘盈利证据。
- 明确未成交/穿价/同根止损假设；补充“同成交条件仅成本加倍”压力，避免改变成交样本掩盖费用损失。75项相关测试通过，重复运行常规指标一致。
- 未计算B绩效、未开启预留、未新增Jev请求。原50%以上净胜率目标仍未达到。
- 入口 `scripts/run_passive_research.py`；报告 `reports/jev-passive-20260927/PROGRESS_REPORT.md`。下一轮独立检验Jev对限价条件净盈利筛选，训练选阈值后冻结，不能放宽样本/压力门槛。

## Jev 持续盈利迭代（2026-09-27，进行中）

- 用户要求继续迭代至达标；目标保持活跃，尚未找到可信盈利版本，不下单。
- 完成亏损归因与成本感知96组、延续确认36组两轮实验；均未通过训练/A验收，本轮未计算B绩效，也未开启新8币预留评估。没有新增Jev请求。
- 58项相关测试通过；24组A组合指标用干净HEAD回测器重放一致。
- 入口 `scripts/run_cost_aware_research.py`，确认模式 `--confirmed-retest`；报告 `reports/jev-profit-iteration-20260927/PROGRESS_REPORT.md`。
- 下一步检验被动限价执行假设，必须真实建模成交与未成交，不能仅降低费率假装盈利；仍保留压力/样本门槛。

## Jev 盈利优先迭代（2026-09-26）

- 192组四类入场策略；补齐真实资金费，保留原组合风控。Jev实际137次预测，冻结阈值0.25。
- **没有找到可信盈利版本**：Jev开发A35笔、PF0.598、100→94.29U；B48笔、PF0.394、100→87.11U；压力测试也失败。
- 新8币112896根5m历史预留数据已校验，研究流程未进行其绩效评估；原有区间全部作为开发数据，不再称独立留出。
- 172项相关测试通过；缓存重复运行结果一致；用HEAD回测器排除原有未提交streak-flip修改后，8组组合指标与标签一致。
- 入口 `scripts/run_jev_profit_research.py`；协议 `docs/research/JEV_PROFIT_PROTOCOL.md`；复现 `docs/research/JEV_PROFIT_RESEARCH.md`；结果 `reports/jev-profit-20260926/FINAL_REPORT.md`。不下单，未证明前向盈利。

## Jev 超短线优化（2026-09-26）

- 真实5m数据已补齐：8币、60天、138240根已收盘K，缺K为0。
- 新增5m双EMA压缩突破/回踩、0.75/1/1.5R止盈、15/30/60min精确到期；复用原组合模拟器，保留原默认行为。
- 144组训练筛选，12组验证；策略和Jev阈值冻结后才处理09-11至09-26留出标签。预测默认全量，不用未来总信号数抽样。
- 111项相关测试通过；另对仅包含本轮修改的暂存回测器做兼容检查，不把旧streak-flip未提交改动混入提交。
- 入口 `scripts/run_jev_scalp_research.py`，说明 `docs/research/JEV_SCALP_RESEARCH.md`。留出已完成：Jev119笔/69赢=57.98%，基线51.45%；但100U→86.91U、PF0.614，盈利门槛未通过。944次真实预测；报告 `reports/jev-scalp-20260926/FINAL_REPORT.md`。干净暂存代码复核两期8组指标全部一致。

## Jev 均线研究（2026-09-26）

- 已复用 JevPlay 的 System One 接口，密钥仅由环境变量或显式本地 env 文件读取。
- 新入口：`scripts/run_jev_ma_research.py`；默认日线/4H双EMA趋势 + 15m六线密集，`dual_ma` 可选旧双EMA回踩路径。
- 退出协议固定为3R、原止损、24h时间K收盘（最长24h15m）；Jev概率阈值固定0.60。不是旧滚仓最优参数重跑。
- 模型输入仅已收盘历史；概率预测与真实净盈利标签分开；缓存按请求/模型/代码哈希，失败不降级为假结果。
- 已通过74项相关测试；真实API已连通。600次真实预测与回测已完成：独立信号成功167/600（27.83%）；两段组合胜率25.35%/27.03%，PF0.637/0.613。Jev固定0.60阈值无成交，不能证明提升。报告：`reports/jev-ma-20260926/FINAL_REPORT.md`。
- 说明：`docs/research/JEV_MA_RESEARCH.md`。研究不改观察台、不下单。

## Binance V1（历史）

- Docker Desktop 与 Freqtrade 2026.5.1 环境已验证。
- V1 六均线 + 回踩已完成研究；**2026 留出未通过，不应实盘**。
- 详见 `reports/FINAL_REPORT.md`。

## OKX 短线（当前）

- 数据：全市场 1H 404 + 高频研究队列 118 已齐。
- 压缩/突破 V2：正式研究失败（交易过稀）。
- 双均线+滚仓：正式 32 候选失败（交易够、PF≈0.74 不够）。
- **当前证据下不可实盘。** 下一棒应提 PF，不放宽硬门槛。
- 详见：`PROJECT_CONTEXT.md`

## 常用命令

```bash
docker compose config --quiet
docker compose run --rm --no-deps --entrypoint python freqtrade -m unittest discover -s tests -v

# OKX 双均线研究
docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/run_okx_v2_research.py --signal-mode dual_ma \
  --output-dir user_data/backtest_results/okx-v2-dualma-full --signal-workers 4
```

## 关键文件

- 交接：`PROJECT_CONTEXT.md`
- OKX 设计/计划：`docs/superpowers/specs|plans/2026-08-10-okx-shortline-v2*`
- 双均线引擎：`user_data/strategy_lib/dual_ma_signal_engine.py`
- 研究入口：`scripts/run_okx_v2_research.py`
- 最新失败产物：`user_data/backtest_results/okx-v2-dualma-full/`
