# PROJECT_CONTEXT（GPT / 下一任 Agent 交接）

更新时间：2026-08-11（六均线多周期半年研究已完成）
分支：`feat/v1-backtest`
交接原因：Codex 额度用尽 → Cursor 续跑 → 用户要求写成文档交 GPT 继续。

---

## 0. 一句话现状

OKX U 本位永续短线系统已完成数据管道与三套信号路径研究：

1. **V2 压缩/突破状态机** → **暂不可行**（有效成交太少）
2. **双均线 + 回踩 + 滚仓** → 交易数已够（≥300），但 **PF 过不了硬门槛**（最佳约 0.74，要求 ≥1.15）→ **当前仍不可实盘**
3. **4H/15m 六均线多周期** → 最近半年 32 组参数均未过门；冻结候选留出期 84 笔、PF 0.750、100→92.66 → **当前仍不可实盘**

**硬门槛一律不放水。** 未全部通过时不得生成“可实盘配置”。

---

## 1. 用户锁定约束（不要再问、不要改成功线）

- 交易所：欧易 OKX USDT 永续（`SWAP`）
- 上市满 30 天才可交易；BTC/ETH **只做方向参考，永不交易**
- 动态前 30 币（按小时、因果、无未来函数）
- 最多 3 仓，同向最多 2；单笔风险 0.75%，组合开放风险 ≤2%
- 退出：2R 分批 40% + 保本 + EMA20 跟踪；6/12/24h 时间门
- 同时比较纯复利 vs 风险封顶
- 通过线：
  - OOS PF ≥ 1.15
  - 回撤 ≤ 35%
  - OOS 交易 ≥ 300
  - 最差主窗口 PF ≥ 0.95
  - 双倍成本 PF ≥ 1.05
- 全部不过 → 明确“暂不可行”，不准硬选一个包装成可行

资产类别：

- 加密合约：自身 4H 趋势 + BTC/ETH 过滤
- 股票/商品合约：**不要**用 BTC 过滤（如 KO）；只用自身 4H
- KO 因上市未满 30 天当时正确排除

用户后续补充目标：围绕原博主的六根均线，优先测试“4H 六线密集并突破定方向，15m 六线密集后入场”，时间范围以最近半年/数月为主。

---

## 2. 本会话实际做了什么

### 2.1 从 Codex 断点接手

- 找到原对话：`8e5c5565-995b-48d4-9006-c9bc1447cddd`
- 当时已完成：全市场 1H 404/404、高频队列 118/118（15m+1H+4H+funding）
- 未提交加固：缺口止损按更差开盘、缺失资金费不利计提、盘中不利价回撤

### 2.2 数据校验

- 118 合约高频完整性：`15m:1h ≈ 4.000`，无重复时间戳
- 产物：`reports/okx-v2/hf-integrity-summary.csv`

### 2.3 正式 V2（压缩突破）研究

命令（已跑完）：

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/run_okx_v2_research.py \
  --output-dir user_data/backtest_results/okx-v2-full \
  --signal-workers 4
```

结果：`research_failed`，32/32 未过门  
主因：**交易过稀**（验证窗中位 2 笔、最高 20；要求 ≥300）  
报告：`reports/okx-v2/FINAL_REPORT.md`（对应压缩突破版本结论）

### 2.4 近半年诊断

- 增加研究脚本日期裁剪：`--start/--end/--holdout-days/--warmup-days/--purpose`
- 跑了 `purpose=half_year_diagnose`，**不能当实盘证据**（撑不起 12m+3m 走样）
- 结果仍更稀：最佳全时段约 7 个事件

产物：`user_data/backtest_results/okx-v2-halfyear/`

### 2.5 双均线 + 滚仓（当前主线）

新增：

- `user_data/strategy_lib/dual_ma_signal_engine.py`
  - 1H EMA 快/慢线定方向
  - 15m 首次回踩快线入场（臂线不可同根回踩）
  - 止损在慢线外侧 + ATR buffer
  - 保留 Top30 / 4H / BTC·ETH 市场过滤（加密）
- `tests/test_dual_ma_signal_engine.py`
- `v2_backtester.py` 增加滚仓：
  - `pyramid_enabled/trigger_r/risk_fraction/max_pyramids`
  - 盈利达触发 R 后加仓，组合风险仍 ≤2%，加仓后止损抬保本
- `walk_forward.py` 增加 `dual_ma_candidate_grid()`
- `scripts/run_okx_v2_research.py` 增加 `--signal-mode dual_ma|v2`

冒烟：`okx-v2-dualma-smoke`  
→ OOS 约 711–777 笔，事件 ~1.8 万；PF≈0.66–0.72 未过门

正式 32 候选：**已跑完** `okx-v2-dualma-full`

| 指标 | 最佳候选（#32） | 门槛 |
|------|----------------|------|
| trades | 317 | ≥300 ✅ |
| PF | 0.738 | ≥1.15 ❌ |
| 最差窗 PF | 0.738 | ≥0.95 ❌ |
| stress PF | 0.579 | ≥1.05 ❌ |
| 回撤 | 30.6% | ≤35% ✅ |
| 伪留出 | 353 笔，PF 0.767，权益 100→75.7 | — |

**结论：双均线路径解开了“没单”，卡在“不赚钱（PF）”。**

伪留出拆解要点：

- long/short **两边都亏**（short 笔数更多、亏更多）
- 主要亏损来自 `initial_stop`；盈利主要来自 `trailing_stop` / `fixed_5r`
- `fixed5` 退出变体相对最好，但 PF 仍 <0.89
- 滚仓开/关对平均 PF 几乎无差
- 正式候选中 `9/21` 的验证 PF 最高；`10/30` 次之，不能写成 `10/30` 优于 `9/21`

### 2.6 4H/15m 六均线多周期（最新）

新增：

- `user_data/strategy_lib/six_ma_mtf_signal_engine.py`
  - 4H：MA20/60/120 + EMA20/60/120 六线 ATR 压缩，后续已收盘 4H 突破才武装方向
  - 15m：对比“再次六线压缩后突破”与“第一次回踩六线带后收回”
  - 信号只在 K 线收盘确认，外层于下一根 15m 开盘执行
  - 保留动态 Top30、BTC/ETH 加密市场过滤、多空和组合风险限制
- `tests/test_six_ma_mtf_signal_engine.py`：13 项因果/多空/首次回踩/跨周期测试
- `walk_forward.py`：六均线 32 组平衡搜索与 4608 组穷举入口
- `scripts/report_okx_v2.py`：支持六均线报告，并把留出期交易数和资金费率覆盖纳入报告通过条件

最近半年平衡研究：`user_data/backtest_results/okx-v2-sixma-recent6m/`

- 研究范围：2026-02-10 至 2026-08-10；最后 60 天完全留出
- 32/32 均失败；没有生成实盘配置
- 冻结候选：4H/15m 压缩均为 2.0 ATR，突破均为 0.05 ATR，嵌套突破，3x
- 选择段：50 笔，PF 1.137，双倍成本 PF 0.740；PF、交易数、压力 PF 三项失败
- 留出段：84 笔，PF 0.750，最大回撤 11.44%，100→92.66，最长连续亏损 6
- 留出段 long：51 笔，PF 约 0.507，净 -9.11；short：33 笔，PF 约 1.163，净 +1.77
- short-only 是看过留出结果后的事后发现，样本只有 33 笔，不能当作新的独立验证
- 报告：`reports/okx-v2-sixma-recent6m/FINAL_REPORT.md`

**最新结论：原版六均线逻辑可以作为低频扫描器继续观察，但当前数据不支持用于 100U→10000U 的实盘复利计划。**

---

## 3. 关键路径与命令

### 文档

- 设计：`docs/superpowers/specs/2026-08-10-okx-shortline-v2-design.md`
- 计划：`docs/superpowers/plans/2026-08-10-okx-shortline-v2.md`
- 本交接：`PROJECT_CONTEXT.md`（本文件）
- 旧进度：`PROJECT_PROGRESS.md`（偏 Binance V1）

### 数据

- 全市场 1H：`user_data/data/okx_v2_hourly/`（404）
- 高频研究：`user_data/data/okx_v2/`（118 合约 15m/1h/4h/funding + `universe_top30.feather`）
- 队列：`user_data/okx_v2_research_cohort.json`（116 trade + BTC/ETH）
- 快照：`config/okx_v2_universe.json`

### 研究产物目录

| 目录 | 含义 |
|------|------|
| `user_data/backtest_results/okx-v2-full/` | V2 压缩突破正式（失败） |
| `user_data/backtest_results/okx-v2-halfyear/` | 近半年诊断（失败） |
| `user_data/backtest_results/okx-v2-dualma-smoke/` | 双均线冒烟 |
| `user_data/backtest_results/okx-v2-dualma-full/` | 双均线正式 32 候选（失败，交易够 PF 不够） |
| `reports/okx-v2/FINAL_REPORT.md` | 压缩突破版中文结论（**不是**双均线最新结论） |

### 常用命令

```bash
# 测试（在 Docker 内；scripts/ 可能只读，不要依赖写 pyc）
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest discover -s tests -v

# 双均线正式研究
docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/run_okx_v2_research.py \
  --signal-mode dual_ma \
  --purpose formal \
  --output-dir user_data/backtest_results/okx-v2-dualma-full \
  --signal-workers 4

# 近半年诊断示例
docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/run_okx_v2_research.py \
  --signal-mode dual_ma \
  --start 2026-02-10 --end 2026-08-11 --holdout-days 30 \
  --purpose half_year_diagnose \
  --output-dir user_data/backtest_results/okx-v2-halfyear-dualma

# 报告（Pillow 在 Codex runtime，不在 freqtrade 容器）
/Users/syndred/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  scripts/report_okx_v2.py \
  --research-dir user_data/backtest_results/okx-v2-dualma-full \
  --output-dir reports/okx-v2-dualma
```

运行环境注意：

- 研究/ Feathe r：优先 Docker `freqtrade`
- 图表报告：本机 Codex Python runtime（容器缺 Pillow 绘图路径）
- 勿并发多个下载容器写同一数据目录

---

## 4. Git 状态与提交

- Cursor 双均线基线与交接已经提交：`f4cf071 research: record dual-ma negative baseline`
- Cursor 之前的因果研究队列提交：`2741eac feat: finalize causal OKX research cohort`
- 六均线多周期代码、测试、报告与本交接文档应作为本轮独立提交保存。
- `user_data/backtest_results/` 继续被忽略；可复现实验结果，不把大量中间文件塞进 Git。
- `reports/okx-v2-sixma-recent6m/` 是本轮需保留的轻量最终报告、资金曲线、交易和参数摘要。
- 根目录 `logs/` 已加入忽略，避免运行日志污染工作树。

---

## 5. 建议 GPT 下一步（按优先级）

目标：不复用已经看过的留出期继续倒推参数；用真正的新数据确认空单优势是否存在。

1. **冻结当前六均线候选做前向模拟**：both / long-only / short-only 三个只读信号账本并行记录 60–90 天；不因中途表现改参数。
2. **空单只能视为新假设**：本轮 short 仅 33 笔且是看完留出后发现，必须用下一段时间验证，不能回写成本轮成功。
3. **退出研究**：标准退出 PF 0.750；`fixed5 + 无时间退出` 也只有 PF 1.023，且仅 50 笔完成、跳过 45 个信号，不能作为优胜方案。可在新前向期预先固定对照。
4. **优先补实盘模型缺口**：历史下架合约的时点币池、mark price / 强平与维护保证金、合约 `minSz/lotSz/ctVal`、费用/滑点计入最坏风险、回测和 Freqtrade 共用日损/组合风险账本。
5. **不要扩大杠杆追终值**：3x/5x/10x 在相同止损风险下不会把负期望变正；100→10000 只能是长期结果指标，不是选参目标。

通过后才做：

- Freqtrade 策略适配六均线信号
- lookahead-analysis + dry-run 文档

---

## 6. 设计/代码坑（踩过，勿重复）

- 容器里 `scripts/` 只读 → `py_compile` 写 pyc 会报 Read-only；用 `ast.parse` 或跑 unittest
- OKX 15m feather 与重采样时间精度混用需统一到 ns UTC（已修）
- Top30 历史必须含“当时已满 30 天”+ 因果稳定入榜（≥360h/30d）；否则昙花一现币爆炸
- 压缩突破路径成交过稀；双均线路径过密且期望为负
- BTC/ETH 勿进可交易候选
- 非加密合约禁止套 BTC/ETH 方向
- `report_okx_v2.py` 默认读 `okx-v2` 目录；双均线结果要显式传 `--research-dir`

---

## 7. 给接手 Agent 的开工清单

```text
1. 读本文件 + docs/superpowers/specs/2026-08-10-okx-shortline-v2-design.md
2. 先读 reports/okx-v2-sixma-recent6m/FINAL_REPORT.md，再读对应
   candidate-results.csv、option-comparison.csv、trades.csv
3. 不要重下数据（118/404 已齐）
4. 不要继续复用 2026-06-11 至 2026-08-10 留出期选参；它已经被看过
5. 任何“通过”必须过全部硬门槛；否则写 research-failed，不写实盘配置
```
