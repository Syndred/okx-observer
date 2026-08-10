# 六均线趋势系统 V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可复现的 Freqtrade Docker 回测项目，参数化复刻六均线 V1，并以样本外证据评估 100 USDT 到 10,000 USDT 的可行性。

**Architecture:** 纯 Python 状态机负责无未来数据的信号与密集区边界，Freqtrade 策略适配层负责指标、回调、杠杆和风险仓位；批处理脚本调用官方回测器并把原始结果归一化成排名、资金曲线与中文报告。信号参数选择与资金参数选择分两阶段执行，避免在 2026 留出集上调参。

**Tech Stack:** Freqtrade 2026.5.1、Docker Compose、Python 3.14 容器运行时、pandas、TA-Lib、pytest。

## Global Constraints

- 只使用 SMA20/60/120 与 EMA20/60/120，不加入额外指标或多周期过滤。
- Binance USDT-M Futures、isolated、1h、做多和做空。
- 初始资金 100 USDT；RR 为 3/5/8/10；杠杆为 3x/5x/10x；账户风险为 0.5%/1%/2%/5%。
- 每个新行为严格执行测试先失败、最小实现、测试通过的循环。
- 结果必须区分训练、验证与 2026 留出区间，并保留全部可复现产物。

---

### Task 1: 项目骨架与固定运行环境

**Files:**
- Create: `docker-compose.yml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `user_data/configs/config.base.json`
- Create: `scripts/ft.sh`

**Interfaces:**
- Consumes: Docker Desktop 与官方 `freqtradeorg/freqtrade:2026.5.1` 镜像。
- Produces: `scripts/ft.sh <freqtrade arguments>` 统一命令入口。

- [ ] 用官方 Docker Compose 模板和 `freqtrade new-config` 生成物作为结构参考，写入固定镜像、只读策略代码挂载和持久数据目录。
- [ ] 运行 `docker compose config`，确认配置解析成功。
- [ ] 运行 `scripts/ft.sh --version`，确认输出版本为 2026.5.1。
- [ ] 提交 `chore: initialize pinned freqtrade docker project`。

### Task 2: 六均线状态机

**Files:**
- Create: `tests/test_v1_signal_engine.py`
- Create: `user_data/strategies/v1_signal_engine.py`

**Interfaces:**
- Produces: `V1Parameters`、`scan_v1_setups(dataframe, params) -> DataFrame`，新增 `enter_long`、`enter_short`、`initial_stop_price`、`risk_distance_ratio` 与状态诊断列。

- [ ] 写构造数据测试：没有压缩时不发信号；运行并确认因模块不存在而失败。
- [ ] 最小实现六条均线 spread 判断；运行并确认第一项通过。
- [ ] 写多单“压缩→向上突破→下一根首次回踩→守住上沿”测试；确认信号缺失而失败。
- [ ] 实现多单状态转移并通过测试。
- [ ] 写空单对称测试并确认失败；实现空单状态转移并通过测试。
- [ ] 写突破同 K 线不得入场、超时取消、穿越远侧失效、只允许第一次回踩四项测试；逐项完成红绿循环。
- [ ] 写前缀不变性测试：追加未来 K 线不得改变历史信号；运行全套测试。
- [ ] 提交 `feat: implement first-pullback six-average state machine`。

### Task 3: 风险仓位与 Freqtrade 策略适配

**Files:**
- Create: `tests/test_risk_model.py`
- Create: `user_data/strategies/risk_model.py`
- Create: `user_data/strategies/MA_EMA_Trend_Strategy.py`

**Interfaces:**
- Produces: `collateral_for_risk(equity, risk_pct, stop_distance_ratio, leverage, max_collateral) -> float`；Freqtrade 类 `MA_EMA_Trend_Strategy`。

- [ ] 写手算样例：100U、1% 风险、1% 止损、5x 杠杆应使用 20U 抵押；确认失败。
- [ ] 实现仓位公式和余额上限；验证零/负止损距离拒绝交易并运行测试。
- [ ] 写策略导入和人工 DataFrame 集成测试；确认策略类或信号列缺失而失败。
- [ ] 使用官方 advanced strategy 模板的回调签名实现指标、长短信号、isolated leverage、风险抵押、密集区止损与固定 RR 退出。
- [ ] 在容器内运行 `pytest -q` 和 `scripts/ft.sh list-strategies`。
- [ ] 提交 `feat: adapt v1 signals to freqtrade risk callbacks`。

### Task 4: 数据清单与历史下载

**Files:**
- Create: `config/pairs.requested.txt`
- Create: `scripts/discover_pairs.sh`
- Create: `scripts/download_data.sh`
- Create: `reports/data-availability.csv`

**Interfaces:**
- Produces: 可用的静态 `PAIR/USDT:USDT` 清单和每币种首尾 K 线时间。

- [ ] 调用 Freqtrade `list-pairs` 获取 Binance swap 市场，映射主流与目标山寨币，记录改名、退市和不可用币。
- [ ] 下载 2021-01-01 至 2026-08-10 的 1h futures candles、mark 与 funding_rate 数据。
- [ ] 运行 `list-data --show-timerange` 并从真实数据生成 `reports/data-availability.csv`。
- [ ] 提交脚本、清单和小型可用性报告；K 线大文件保持不入 Git。

### Task 5: 冒烟回测与参数搜索

**Files:**
- Create: `tests/test_result_metrics.py`
- Create: `scripts/run_backtest_matrix.py`
- Create: `scripts/extract_metrics.py`
- Create: `user_data/configs/config.backtest.json`
- Create: `reports/search-results.csv`

**Interfaces:**
- Produces: 标准化指标行，包含参数、时期、交易数、收益、Profit Factor、最大回撤、最大连续亏损和币种分散度。

- [ ] 写包含已知盈亏序列的结果解析测试，手算验证胜率、平均盈亏、Profit Factor 与最大连续亏损；确认失败。
- [ ] 实现结果解析器并通过测试。
- [ ] 先在 BTC/ETH 的 2025-01 短区间执行一次 1h 冒烟回测，确认有信号列、策略可加载、结果 ZIP 可读取。
- [ ] 在训练集执行信号参数和 RR 网格；每次使用 `--cache none --export trades` 并记录完整命令与输出文件。
- [ ] 使用验证集筛除交易数过少、Profit Factor 不稳定、回撤失控和单币贡献过高的候选，保留排名前列参数。
- [ ] 提交代码与 `reports/search-results.csv`，原始 ZIP 保留在结果目录。

### Task 6: 杠杆风险矩阵与留出检验

**Files:**
- Create: `reports/capital-matrix.csv`
- Create: `reports/holdout-results.csv`
- Create: `reports/equity-curves/`

**Interfaces:**
- Consumes: Task 5 冻结的候选信号参数。
- Produces: 3x/5x/10x × 0.5%/1%/2%/5% 的真实 Freqtrade 结果和资金曲线。

- [ ] 对冻结候选运行训练+验证资金矩阵，起始余额固定为 100 USDT、最多 3 个并发仓位。
- [ ] 仅按预先定义的联合评分选择最终参数，不读取 2026 留出表现调参。
- [ ] 对最终参数执行一次 2026 留出回测，生成多空、币种和年份分解。
- [ ] 从交易时间序列计算首次达到 1,000U/10,000U、50% 回撤和不可交易余额事件，并生成 PNG 资金曲线。
- [ ] 提交 CSV、PNG 和冻结参数文件。

### Task 7: 复核与中文交接

**Files:**
- Create: `reports/FINAL_REPORT.md`
- Create: `PROJECT_PROGRESS.md`
- Modify: `README.md`

**Interfaces:**
- Produces: 最优参数建议、可行性结论、限制、复现命令和后续迭代入口。

- [ ] 逐项核对用户要求与三段回测证据，明确交易所数据缺口和资金费率处理。
- [ ] 运行完整 `pytest -q`、Compose 配置检查、策略加载检查，并重新执行最终留出回测。
- [ ] Review Git diff、结果文件引用和资金曲线是否可打开，修正发现的问题。
- [ ] 写中文最终报告与进度交接，不把回测收益表述为保证。
- [ ] 提交 `docs: deliver v1 backtest evidence and handoff`。
