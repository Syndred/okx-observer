# OKX 六均线三阶段筛选器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个因果、可复现的 OKX 三阶段六均线筛选器，分别输出双周期缠绕、4H突破后15m缠绕、等待首次回踩和回踩确认四类状态。

**Architecture:** 新建独立纯函数模块正向重建 4H episode 与 15m setup，复用现有六均线、ATR和持续缠绕算法；新扫描脚本只负责下载已确认K线、调用状态机和生成四表报告。旧筛选器和旧报告目录原样保留，新结果写入独立目录。

**Tech Stack:** Python 3.11、pandas、NumPy、Docker、Freqtrade unittest 环境、OKX 公共行情接口。

## Global Constraints

- 只使用 MA20/60/120、EMA20/60/120 和 ATR 标准化，不加入 ADX、成交量等信号过滤。
- 4H K 仅在 `date + 4h` 后可用；日线仅在 `date + 1d` 后可用；所有状态只使用已收盘K线。
- 多空规则镜像；突破K不能兼任回踩K；第一次触碰失败后不得等待第二次。
- Stage 1/2 不要求日线趋势准入；日线只加分或降级。BTC/ETH 过滤仅用于加密类 Stage 3B。
- 旧 `okx_trend_compression_screener.py`、旧报告目录和当前未提交快照不得覆盖。
- 不生成自动实盘配置；`ENTRY_CONFIRMED` 仍是人工辅助提示。

---

### Task 1: 三阶段纯状态机

**Files:**
- Create: `user_data/strategy_lib/six_ma_three_stage_screener.py`
- Create: `tests/test_six_ma_three_stage_screener.py`
- Reuse: `user_data/strategy_lib/v2_signal_engine.py`
- Reuse: `user_data/strategy_lib/okx_trend_compression_screener.py`

**Interfaces:**
- Produces `ThreeStageParameters`，包含4H/15m缠绕窗口、密集比例、交叉、漂移、突破、setup寿命、回踩和止损参数。
- Produces `scan_three_stage_pair(*, pair, inst_category, candles15m, candles4h, candles1d, btc4h, eth4h, as_of, params) -> dict[str, object]`。
- 返回字段至少包括 `stage`、`direction`、`daily_bias`、`four_hour_state`、`four_hour_coil_score`、`four_hour_zone_high/low`、`four_hour_breakout_at`、`fifteen_minute_state`、`fifteen_minute_coil_score`、`fifteen_minute_zone_high/low`、`fifteen_minute_breakout_at`、`first_pullback_at`、`initial_stop_price`、`next_executable_at`、`reason`。
- `stage` 只允许 `watch_both_coiled`、`ready_4h_breakout_15m_coiled`、`wait_first_pullback`、`entry_confirmed`、`none`。

- [x] **Step 1: 写 Stage 1 和4H成熟缠绕失败测试**

构造连续4H和15m成熟缠绕，断言 `stage == "watch_both_coiled"` 且无方向、无止损；再构造单根密集、平行贴合、缺中间4H，断言均为 `none`。

- [x] **Step 2: 运行测试确认 RED**

Run:

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest tests.test_six_ma_three_stage_screener -v
```

Expected: FAIL，因为模块或 `scan_three_stage_pair` 尚不存在。

- [x] **Step 3: 最小实现通用缠绕测量与 Stage 1**

实现内部 `CoilParameters`、`CoilEpisode` 和 `_measure_coil_window(frame, timeframe, params)`。15m 使用16根/75%/尾部8根/4次交叉且分布3根/价格中心2次/漂移1 ATR；4H 使用12根/8根密集/尾部4根/3次交叉且分布2根/价格中心2次/漂移1.2 ATR。严格拒绝非连续或重复时间戳。

- [x] **Step 4: 运行 Stage 1 测试确认 GREEN**

Run: 同 Step 2。Expected: Stage 1 相关测试 PASS。

- [x] **Step 5: 写 Stage 2 的 RED 测试**

覆盖4H成熟episode后多/空突破、无历史episode的普通趋势、冻结边界等待超过6根4H、突破反向失效、4H突破前旧15m密集不得追认。

- [x] **Step 6: 实现4H episode和 Stage 2**

episode 首次成熟后以连续密集K的六线包络更新；离开密集状态冻结。冻结后6根已收盘4H内，close 超过近侧边界 `0.10 × ATR4H` 才定方向。4H突破可见后重新确认15m成熟episode，最多等待48根15m。

- [x] **Step 7: 写 Stage 3A/3B 的 RED 测试**

覆盖15m同向突破、反向突破取消、突破K禁止回踩、首次触碰收回、第一次触碰失守后第二次不可复活、12根回踩超时、止损远侧边界、下一根15m执行时间、多空镜像。

- [x] **Step 8: 实现 Stage 3A/3B**

15m episode 用末端连续密集K的 `min(cluster_low)`/`max(cluster_high)`；close 越过方向边界 `0.10 × ATR15` 冻结。首次触碰容差 `0.20 × ATR15`；多单收盘必须 `>= zone_high`，空单必须 `<= zone_low`；止损为远侧边界外 `0.20 × ATR15`。

- [x] **Step 9: 写因果、日线和市场过滤 RED 测试并实现**

覆盖 `as_of` 裁剪、4H exact available-at、未收盘K、未来追加不改历史、日线同向加分/严格反向降级、BTC/ETH仅在加密类 `entry_confirmed` 过滤、非加密跳过市场过滤。

- [x] **Step 10: 运行定向测试和旧信号回归**

Run:

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest tests.test_six_ma_three_stage_screener \
  tests.test_six_ma_mtf_signal_engine \
  tests.test_okx_trend_compression_screener -v
```

Expected: 全部 PASS，无 warning/error。

- [x] **Step 11: 提交**

```bash
git add user_data/strategy_lib/six_ma_three_stage_screener.py \
  tests/test_six_ma_three_stage_screener.py
git commit -m "feat: add three-stage six-ma signal state machine"
```

---

### Task 2: OKX 实时扫描和四阶段报告

**Files:**
- Create: `scripts/scan_okx_three_stage.py`
- Create: `scripts/scan_okx_three_stage.sh`
- Create: `tests/test_scan_okx_three_stage.py`
- Create: `reports/okx-three-stage-screener/README.md`
- Reuse: `scripts/scan_okx_trend_compression.py`
- Reuse: `scripts/snapshot_okx_instruments.py`

**Interfaces:**
- `markdown_report(results: pd.DataFrame, retrieved_at: datetime, params: ThreeStageParameters, total_live: int, errors: dict[str, str]) -> str`。
- CLI 默认输出 `reports/okx-three-stage-screener/LATEST.md`、`latest.csv`、`run-manifest.json`。
- 输出CSV每个合约一行；`stage_order` 仅用于排序，不落盘。

- [x] **Step 1: 写报告和分阶段扫描 RED 测试**

使用真实 `scan_three_stage_pair` 输出fixture，断言四张表顺序固定为确认开仓、等待回踩、准备突破、双周期缠绕；`none` 只进入诊断CSV。断言 Stage 1/2 即使日线中性仍被调用，BTC/ETH不出现在交易候选。

- [x] **Step 2: 运行测试确认 RED**

Run:

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest tests.test_scan_okx_three_stage -v
```

Expected: FAIL，因为扫描脚本不存在。

- [x] **Step 3: 实现分阶段下载与报告**

下载所有合资格交易合约的150根日线和150根4H；4H当前成熟或最近有效突破的合约再下载150根15m。BTC/ETH 4H独立下载作确认级市场过滤。报告字段与设计文档第8节一致；任何行情错误保留该合约诊断行并写manifest。

- [x] **Step 4: 实现一键脚本和README**

`scan_okx_three_stage.sh` 进入项目根目录后调用Docker扫描；README明确四阶段含义、运行方式、非自动开仓边界和旧筛选器保留路径。

- [x] **Step 5: 运行测试确认 GREEN**

Run: 同 Step 2。Expected: 全部 PASS。

- [x] **Step 6: 提交**

```bash
git add scripts/scan_okx_three_stage.py scripts/scan_okx_three_stage.sh \
  tests/test_scan_okx_three_stage.py reports/okx-three-stage-screener/README.md
git commit -m "feat: add live OKX three-stage screener"
```

---

### Task 3: 实时重扫、人工验收和中文交接

**Files:**
- Create: `reports/okx-three-stage-screener/LATEST.md`
- Create: `reports/okx-three-stage-screener/latest.csv`
- Create: `reports/okx-three-stage-screener/run-manifest.json`
- Modify: `PROJECT_CONTEXT.md`

**Interfaces:**
- Consumes Task 2 CLI和Task 1状态字段。
- Produces可复现的当前行情快照和中文验收结论。

- [x] **Step 1: 运行当前OKX全市场扫描**

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  scripts/scan_okx_three_stage.py \
  --output-dir reports/okx-three-stage-screener \
  --workers 6 --requests-per-second 10
```

Expected: exit 0，manifest保存各阶段数量和请求错误。

- [x] **Step 2: 人工图形验收**

从每个非空阶段取最多3个合约，重新读取其最近150根4H/15m原始K和六线数值，核对：持续密集、冻结区间、突破时间、第一次触碰和报告状态一致。每个高分排除原因也抽查至少3个；当前无某阶段时使用历史回放fixture，不降低阈值。

- [x] **Step 3: 更新中文交接**

在 `PROJECT_CONTEXT.md` 记录新旧筛选器差异、当前各阶段数量、候选名称、运行命令、测试数量和证据边界；不得把当前快照写成盈利证明。

- [x] **Step 4: 全量验证**

```bash
python3 -m py_compile \
  user_data/strategy_lib/six_ma_three_stage_screener.py \
  scripts/scan_okx_three_stage.py \
  tests/test_six_ma_three_stage_screener.py \
  tests/test_scan_okx_three_stage.py
git diff --check
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest discover -s tests -q
```

Expected: 所有命令 exit 0，完整测试0失败。

- [x] **Step 5: 提交结果和交接**

```bash
git add reports/okx-three-stage-screener PROJECT_CONTEXT.md
git commit -m "data: record three-stage screener snapshot"
```
