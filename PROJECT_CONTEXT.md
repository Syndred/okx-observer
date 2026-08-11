# PROJECT_CONTEXT（GPT / 下一任 Agent 交接）

更新时间：2026-08-11 08:35 CST  
分支：`feat/v1-backtest`  
交接原因：Codex 额度用尽 → Cursor 续跑 → 用户要求写成文档交 GPT 继续。

---

## 0. 一句话现状

OKX U 本位永续短线系统已完成数据管道与两套信号路径研究：

1. **V2 压缩/突破状态机** → **暂不可行**（有效成交太少）
2. **双均线 + 回踩 + 滚仓** → 交易数已够（≥300），但 **PF 过不了硬门槛**（最佳约 0.74，要求 ≥1.15）→ **当前仍不可实盘**

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

用户后续补充目标：**在双均线系统 + 滚仓前提下找最优解，测试到过硬门槛为止**（已选定用双均线替换压缩/突破核心）。

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

## 4. Git 状态（未提交，接手后勿盲目 commit）

已修改：

- `scripts/run_okx_v2_research.py`（日期裁剪、funding 覆盖、并发扫信号、`--signal-mode dual_ma`）
- `user_data/strategy_lib/v2_backtester.py`（不利跳空、imputed funding、滚仓）
- `user_data/strategy_lib/walk_forward.py`（`dual_ma_candidate_grid`）
- `tests/test_v2_backtester.py`

未跟踪：

- `user_data/strategy_lib/dual_ma_signal_engine.py`
- `tests/test_dual_ma_signal_engine.py`
- `PROJECT_CONTEXT.md`
- `reports/okx-v2/*`（含旧 FINAL_REPORT）
- `logs/`（可忽略）
- 大量 `user_data/backtest_results/okx-v2-*` 研究结果

之前 Codex 已有多个 feat commit 在分支上（数据管道、信号、风控、walk-forward 等）。本轮 Cursor 改动**尚未 commit**。

---

## 5. 建议 GPT 下一步（按优先级）

目标：在**不放宽硬门槛**前提下把 PF 从 ~0.74 抬到 ≥1.15，同时保住 trades≥300、DD≤35%。

1. **多空拆分对照**：long-only / short-only（两边都亏，需确认谁拖累、是否单边可过门）
2. **提质过滤网格**（牺牲一些笔数、换 PF）：
   - 强制 `require_4h_trend=True` + 更严 pullback
   - 均线以 `10/30`、`20/60` 为主
   - 滚仓默认关或更晚触发（当前对 PF 几乎无帮助）
   - 退出对照：`fixed5` / 去掉 `no_progress_6h` 或放宽时间门（变体里 fixed5 相对最好，但仍 <0.89）
3. **成本与止损结构**：`initial_stop` 是主亏源 → 查止损是否过近/过远、是否慢线止损被洗
4. **不要再靠“缩短样本”冲过门**；近半年只能诊断密度
5. 若多轮提质后 PF 仍稳定 <1.0：按约定交付**明确暂不可行**，而不是继续无限搜参硬凑

通过后才做：

- `scripts/report_okx_v2.py` 生成双均线版 FINAL_REPORT
- Freqtrade 策略适配 `OKX_Shortline_V2` / 新策略文件接 dual_ma
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
2. 读 user_data/backtest_results/okx-v2-dualma-full/research-failed.json
   与 candidate-results.csv、pseudo-holdout-variants.csv
3. 不要重下数据（118/404 已齐）
4. 从“提 PF”实验继续，勿回到压缩突破主线（除非对照）
5. 任何“通过”必须过全部硬门槛；否则写 research-failed，不写实盘配置
```
