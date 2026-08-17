# OKX 六均线四层机会筛选器

更新时间：2026-08-17T15:36:59.346035+08:00

## 规则摘要

- 高赔率打法：4H 已发散且同向后，15m 第一次回踩 20 均线；止损贴 20 均线，目标取前方最近的 4H/日线密集区中心。计划赔率 < 2R 不进入优先查看。
- 全部信号只使用已收盘K线，核心指标只含 MA20/60/120、EMA20/60/120 与 ATR 标准化。
- 采用 `15m-first`：先识别15m成熟缠绕或正在收拢，再用4H/日线作为高周期上下文，而不是硬删除机会。
- 高周期上下文可为4H成熟/形成、4H与日线趋势同向、仅日线趋势或数据未知；明确反向只阻止最终确认。
- 15m同向突破后只认后续第一次回踩；第一次触碰失守即取消，不等第二次。
- 日线仅作方向偏向，不作为前两阶段硬门槛；BTC/ETH市场过滤只作用于加密类最终确认。
- `entry_confirmed` 仅在下一根15m开盘前有效。本表是人工筛选辅助，不是自动开仓指令，也不是盈利证明。

共检查 431 个 live USDT 永续；行情请求异常 0 个。当前交易合约状态：确认 0、等待回踩 18、成熟观察 24、形成中观察 4。

扫描口径：仅 `instCategory=1` 加密类，排除稳定币基准；仅纳入 Bitget 官方 USDT 永续、状态 normal 且未下线的交集合约。BTC/ETH 以交易角色参与，同时保留市场参考标记。扫描合资格交集：255；排除统计：bitget_unavailable 19、non_crypto_category 156、stablecoin_base 1。

## 1. 回踩确认

当前没有符合该阶段的合约。

## 2. 等待第一次回踩

| 合约 | 方向 | 玩法 | 计划赔率 | 目标 | Bitget可用 | 15m状态/分数 | 收缩/压缩 | 4H上下文/分数 | 风险 | 日线偏向 | 当前收盘相对六线 | 15m窗口末端 | 4H窗口末端 | 4H突破 | 15m突破 | 首次回踩 | 下一可执行时间 | 止损 | 无效原因 |
|---|---|---|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|---:|---|
| PIPPIN-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 84.4 | 0.000 / - ATR | unknown / 0.0 | - | neutral | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:00:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| LAB-USDT-SWAP | 空 | pullback_20 | - | - | 是 | breakout_short / 83.7 | 0.000 / - ATR | trend_aligned / 35.0 | expanded_risk | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T05:45:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| DOT-USDT-SWAP | 空 | pullback_20 | - | - | 是 | breakout_short / 83.7 | 0.000 / - ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:30:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| UB-USDT-SWAP | 空 | pullback_20 | - | - | 是 | breakout_short / 82.0 | 0.000 / - ATR | unknown / 0.0 | daily_not_aligned, daily_strong_unknown, expanded_risk | neutral | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:00:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| LTC-USDT-SWAP | 空 | pullback_20 | - | - | 是 | breakout_short / 81.8 | 0.000 / - ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:00:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| ZEN-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 81.1 | 0.000 / - ATR | opposite / 0.0 | opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T06:45:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| AXS-USDT-SWAP | 空 | pullback_20 | - | - | 是 | breakout_short / 80.6 | 0.000 / - ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T05:30:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| CELO-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 80.0 | 0.000 / - ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:30:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| SUSHI-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 78.4 | 0.000 / - ATR | opposite / 0.0 | opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T06:45:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| FARTCOIN-USDT-SWAP | 多 | pullback_20 | - | - | 是 | breakout_long / 78.3 | 0.000 / - ATR | unknown / 0.0 | daily_not_aligned, expanded_risk | neutral | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T06:45:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| EGLD-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 78.2 | 0.000 / - ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:00:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| HBAR-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 72.0 | 0.000 / - ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:00:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| ORDER-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 71.1 | 0.000 / - ATR | opposite / 0.0 | opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:15:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| LINEA-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 71.1 | 0.000 / - ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:15:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| A-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 70.3 | 0.000 / - ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:00:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| PLUME-USDT-SWAP | 空 | coil_retest | 1.13 | 0.01196013 | 是 | breakout_short / 69.6 | 0.000 / - ATR | opposite / 0.0 | expanded_risk, opposite | long | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:00:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| ZRO-USDT-SWAP | 多 | coil_retest | 0.81 | 0.82033417 | 是 | breakout_long / 66.6 | 0.000 / - ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T07:30:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |
| NEAR-USDT-SWAP | 多 | coil_retest | - | - | 是 | breakout_long / 66.4 | 0.000 / - ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | 2026-08-17T05:15:00+00:00 | - | - | - | fifteen_minute_breakout_waiting_for_pullback |

## 3. 15m成熟缠绕观察

| 合约 | 方向 | 玩法 | 计划赔率 | 目标 | Bitget可用 | 15m状态/分数 | 收缩/压缩 | 4H上下文/分数 | 风险 | 日线偏向 | 当前收盘相对六线 | 15m窗口末端 | 4H窗口末端 | 4H突破 | 15m突破 | 首次回踩 | 下一可执行时间 | 止损 | 无效原因 |
|---|---|---|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|---:|---|
| NOT-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 98.2 | 0.250 / 0.84 ATR | opposite / 0.0 | expanded_risk, opposite | neutral | inside_six_line_band | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| ASTER-USDT-SWAP | 多 | coil_retest | - | - | 是 | mature_coil / 97.3 | 0.332 / 0.78 ATR | opposite / 0.0 | expanded_risk, opposite | short | inside_six_line_band | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| BICO-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 97.1 | 0.529 / 0.49 ATR | unknown / 0.0 | expanded_risk | neutral | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| VIRTUAL-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 96.3 | 0.126 / 1.01 ATR | opposite / 0.0 | opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | 2026-08-17T00:00:00+00:00 | - | - | - | - | fifteen_minute_coil_ready |
| TAO-USDT-SWAP | 多 | coil_retest | - | - | 是 | mature_coil / 94.0 | -0.488 / 1.18 ATR | opposite / 0.0 | opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| WIF-USDT-SWAP | 多 | coil_retest | - | - | 是 | mature_coil / 92.9 | -0.329 / 1.10 ATR | opposite / 0.0 | expanded_risk, opposite | short | inside_six_line_band | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| ZK-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 92.3 | -0.217 / 0.95 ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| MOODENG-USDT-SWAP | 多 | coil_retest | - | - | 是 | mature_coil / 89.3 | 0.294 / 1.50 ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| PARTI-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 88.0 | 0.426 / 0.88 ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| TURBO-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 85.5 | 0.413 / 0.96 ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| PI-USDT-SWAP | 多 | coil_retest | - | - | 是 | mature_coil / 84.8 | 0.373 / 1.25 ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| WOO-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 83.7 | -0.116 / 1.44 ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| ZAMA-USDT-SWAP | 多 | coil_retest | - | - | 是 | mature_coil / 83.1 | 0.082 / 1.39 ATR | opposite / 0.0 | expanded_risk, opposite | neutral | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| MERL-USDT-SWAP | neutral | none | - | - | 是 | mature_coil / 83.0 | 0.391 / 1.01 ATR | mature_coil / 34.2 | - | neutral | inside_six_line_band | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| GALA-USDT-SWAP | neutral | none | - | - | 是 | mature_coil / 82.6 | -0.340 / 1.78 ATR | trend_aligned / 35.0 | expanded_risk | short | inside_six_line_band | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| BLUR-USDT-SWAP | 多 | coil_retest | - | - | 是 | mature_coil / 82.0 | 0.310 / 0.94 ATR | opposite / 0.0 | expanded_risk, opposite | short | inside_six_line_band | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| ANIME-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 81.8 | 0.567 / 0.52 ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | below_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| RAY-USDT-SWAP | 空 | coil_retest | - | - | 是 | mature_coil / 78.6 | -0.035 / 1.56 ATR | opposite / 0.0 | expanded_risk, opposite | short | above_all | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | 2026-08-17T00:00:00+00:00 | - | - | - | - | fifteen_minute_coil_ready |
| IOST-USDT-SWAP | neutral | none | - | - | 是 | mature_coil / 77.1 | 0.316 / 1.74 ATR | daily_trend_only / 20.0 | expanded_risk, higher_timeframe_unconfirmed | short | inside_six_line_band | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |
| ONT-USDT-SWAP | 多 | coil_retest | - | - | 是 | mature_coil / 75.2 | 0.230 / 1.44 ATR | forming_coil / 22.2 | - | neutral | inside_six_line_band | 2026-08-17T07:30:00+00:00 | 2026-08-17T04:00:00+00:00 | - | - | - | - | - | fifteen_minute_coil_ready |

## 4. 15m正在收拢观察

当前没有符合该阶段的合约。

## 5. 连续涨跌 / 放量异动

| 合约 | 状态 | 方向 | 日线连续 | 4H连续 | 量能倍数 | 振幅/ATR | 24h |
|---|---|---|---:|---:|---:|---:|---:|
| DOT-USDT-SWAP | 连跌、量能未放大 | 空 | 6 | 1 | 0.53 | 0.55 | 0.04% |
| UB-USDT-SWAP | 连跌、量能未放大 | 空 | 3 | 2 | 0.09 | 0.26 | -0.56% |
| LTC-USDT-SWAP | 连涨、量能未放大 | 空 | 2 | 1 | 0.50 | 0.71 | -0.23% |
| ZEN-USDT-SWAP | 连跌、量能未放大 | 多 | 3 | 1 | 0.41 | 0.52 | 1.18% |
| AXS-USDT-SWAP | 连跌、量能未放大 | 空 | 2 | 1 | 0.62 | 0.70 | -0.02% |
| EGLD-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.30 | 0.60 | 0.53% |
| HBAR-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.72 | 0.56 | 0.64% |
| A-USDT-SWAP | 连跌、量能未放大 | 多 | 8 | 1 | 0.36 | 0.56 | 0.44% |
| ZRO-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.35 | 0.56 | 2.61% |
| NEAR-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.42 | 0.59 | 1.87% |
| ASTER-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.48 | 0.52 | -0.07% |
| TAO-USDT-SWAP | 连跌、量能未放大 | 多 | 3 | 1 | 0.41 | 0.42 | 0.71% |
| WIF-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.47 | 0.63 | 0.44% |
| MOODENG-USDT-SWAP | 连跌、量能未放大 | 多 | 3 | 1 | 0.40 | 0.83 | -0.45% |
| PARTI-USDT-SWAP | 连涨、量能未放大 | 空 | 2 | 4 | 0.45 | 0.79 | 1.55% |
| PI-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.38 | 0.42 | -0.18% |
| ZAMA-USDT-SWAP | 连跌、量能未放大 | 多 | 3 | 2 | 0.23 | 0.58 | 1.86% |
| MERL-USDT-SWAP | 连涨、量能未放大 | 待定 | 2 | 1 | 0.52 | 0.64 | 0.54% |
| GALA-USDT-SWAP | 连跌、量能未放大 | 待定 | 2 | 1 | 0.78 | 0.88 | -0.60% |
| BLUR-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.29 | 0.49 | -0.30% |
| RAY-USDT-SWAP | 连跌、量能未放大 | 空 | 2 | 1 | 0.40 | 0.74 | 0.29% |
| ONT-USDT-SWAP | 连续上涨且放量 | 多 | 2 | 1 | 8.05 | 1.87 | 3.12% |
| COAI-USDT-SWAP | 连跌、量能未放大 | 多 | 2 | 1 | 0.12 | 0.47 | -0.09% |
| TIA-USDT-SWAP | 连跌、量能未放大 | 空 | 3 | 1 | 1.23 | 1.10 | 0.27% |

## 参数口径

- `coil_window_4h` = 12
- `compression_4h_atr` = 1.6
- `min_compressed_fraction_4h` = 0.5833333333333334
- `min_trailing_compressed_bars_4h` = 3
- `min_line_crossings_4h` = 2
- `min_line_crossing_bars_4h` = 2
- `min_center_crossings_4h` = 1
- `max_center_drift_4h_atr` = 1.8
- `coil_window_4h_forming` = 8
- `compression_4h_forming_atr` = 2.4
- `min_compressed_fraction_4h_forming` = 0.5
- `min_trailing_compressed_bars_4h_forming` = 2
- `min_line_crossings_4h_forming` = 1
- `min_line_crossing_bars_4h_forming` = 1
- `min_center_crossings_4h_forming` = 0
- `min_contraction_ratio_4h_forming` = 0.2
- `max_center_drift_4h_forming_atr` = 2.4
- `coil_window_15m` = 16
- `compression_15m_atr` = 2.0
- `min_compressed_fraction_15m` = 0.625
- `min_trailing_compressed_bars_15m` = 4
- `min_line_crossings_15m` = 3
- `min_line_crossing_bars_15m` = 2
- `min_center_crossings_15m` = 2
- `max_center_drift_15m_atr` = 1.4
- `coil_window_15m_forming` = 8
- `compression_15m_forming_atr` = 2.4
- `min_compressed_fraction_15m_forming` = 0.5
- `min_trailing_compressed_bars_15m_forming` = 2
- `min_line_crossings_15m_forming` = 1
- `min_line_crossing_bars_15m_forming` = 1
- `min_center_crossings_15m_forming` = 0
- `min_contraction_ratio_15m_forming` = 0.2
- `max_center_drift_15m_forming_atr` = 1.8
- `breakout_4h_atr` = 0.1
- `zone_breakout_wait_4h` = 6
- `setup_wait_15m` = 48
- `breakout_15m_atr` = 0.1
- `pullback_wait_15m` = 12
- `pullback_touch_atr` = 0.2
- `stop_buffer_atr` = 0.2
- `history_min_15m` = 120
- `history_min_15m_forming` = 127
- `history_min_15m_mature` = 135
- `history_min_4h` = 120
- `history_min_4h_forming` = 127
- `history_min_4h_mature` = 131
- `history_min_1d` = 63
- `history_min_1d_strong` = 120
- `max_observation_items` = 20
- `min_planned_rr` = 2.0

观察层合计纳入 20 条（上限 20 条）；诊断 CSV 保留全部交易合约与排除原因。
