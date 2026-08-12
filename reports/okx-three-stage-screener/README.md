# OKX 六均线三阶段筛选器

这套筛选器用于解决“在大量合约里寻找六均线缠绕机会”的问题，不自动下单。

四类状态：

1. `entry_confirmed`：15分钟突破后的第一次回踩已收回，仅提示下一根15分钟开盘。
2. `wait_first_pullback`：15分钟已突破，等待第一次回踩。
3. `ready_4h_breakout_15m_coiled`：4小时缠绕后突破，15分钟重新缠绕。
4. `watch_both_coiled`：4小时和15分钟当前同时缠绕，尚未选择方向。

运行：

```bash
./scripts/scan_okx_three_stage.sh
```

结果保存在本目录的 `LATEST.md`、`latest.csv` 和 `run-manifest.json`。旧版筛选结果仍保留在 `reports/okx-screener/`，不会被覆盖。

筛选只使用已收盘K线和 MA/EMA 20、60、120 加 ATR 标准化。日线只作为偏向，BTC/ETH 方向过滤只在加密合约最终回踩确认时生效。当前快照不是回测盈利证明，也不构成实盘建议。
