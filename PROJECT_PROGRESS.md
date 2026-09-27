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
- 结果、训练门槛及亏损归因见 `reports/jev-passive-long-desktop-20260927/PROGRESS_REPORT.md`。下一轮研究假设为将目标从2R固定改为1R，其他参数和所有验收门槛不变；先冻结新协议再运行。此改动尚未验证。

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
