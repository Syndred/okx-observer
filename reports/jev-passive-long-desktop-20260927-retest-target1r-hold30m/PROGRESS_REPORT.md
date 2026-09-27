# Jev 长历史限价筛选：突破回踩信号族实验报告

日期：2026-09-27（UTC）
仓库：`Syndred/okx-observer`，分支 `codex/desktop-jev-research`
代码提交：`0a1b896d8741729b80a7c16c76c5098459616ead`

## 结论

EMA20/60突破回踩信号族在固定训练窗只生成2个挂单，其中仅1笔成交。交易数远低于训练各情景至少80笔的固定门槛；即使该笔成交盈利，也不能支持策略结论。状态为 `no_qualified_training_threshold`。没有计算开发A/B标签、组合验收或预留，也没有实盘订单。

## 环境与数据

本轮使用 Python 3.12.10、pandas 2.3.3、NumPy 2.5.3、pyarrow 24.0.0、requests 2.34.2、pytest 8.4.2。长数据清单 SHA-256 为 `4f388ac2ae520c0a23b7af18c7133cd6dbbaa8083e631cda7e636a981eaf942f`，共488,448根K线和5,088条资金费，`complete=true`；预留价格未读取。

研究协议和入口已在评分前提交并推送。Jev模型 `jev-1.13.0` 对2个训练订单全部返回有效概率：真实API调用2次、缓存命中0次。

## 训练结果

基线2个挂单中1笔成交。常规及两种压力场景均只有1笔成交，净收益均值分别为+0.300%、+0.181%、+0.181%；PF因只有一笔盈利且无亏损样本而为空。单笔结果不满足样本门槛。

Jev阈值0.15和0.25各接受1个挂单，但该挂单未成交；阈值0.35及以上没有挂单。六个阈值全部不合格，训练未满足每种情景至少80笔成交。没有开启开发A/B。

## 亏损归因与下一轮

该实验没有足够成交样本进行收益归因，失败直接来自信号数量不足：三个月训练期八币只生成2个挂单。下一轮拟改用现有5分钟EMA20/60趋势回调信号族：15/60分钟EMA9/21趋势同向，价格回踩EMA20后带方向收盘确认。此族不经过压缩后突破、6根K内回踩的稀少序列，预期能产生更多独立机会；仍需遵守80笔训练门槛。

下一轮只切换信号族，保留1R目标、30分钟持仓、其他费用/资金费/成交规则和门槛。结果未验证；先冻结新协议和代码、跑相关测试并推送，再启动训练评分。

## 产物

- 结果目录（Git 忽略）：`user_data/backtest_results/jev-passive-long-desktop-20260927-retest-target1r-hold30m/`
- 本机日志（Git 忽略）：`work/logs/run_jev_passive_long_desktop_20260927-retest-target1r-hold30m.log`
- 实验前环境记录（Git 忽略）：`work/logs/jev-passive-long-desktop-20260927-retest-target1r-hold30m-preflight.json`
- 冻结协议：`docs/research/JEV_LONG_HISTORY_RETEST_1R_HOLD_30M_PROTOCOL.md`
