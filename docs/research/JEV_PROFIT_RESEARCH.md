# Jev 超短线盈利研究复现

本轮目标与门槛以 [冻结协议](JEV_PROFIT_PROTOCOL.md) 为准，结论见 [完整报告](../../reports/jev-profit-20260926/FINAL_REPORT.md)。当前结果失败，不是实盘配置。

## 运行

在仓库根目录使用已安装研究依赖的Python环境。下载器只读取OKX公开行情；Jev密钥从环境变量 `JEV_API_KEY` 或显式env文件读取，不写入报告。

```bash
.venv/bin/python scripts/download_okx_scalp_data.py
.venv/bin/python scripts/download_okx_scalp_data.py \
  --start 2026-08-08T00:00:00Z --end 2026-09-26T00:00:00Z \
  --symbols SUI-USDT-SWAP NEAR-USDT-SWAP UNI-USDT-SWAP AAVE-USDT-SWAP LTC-USDT-SWAP BNB-USDT-SWAP DOT-USDT-SWAP ATOM-USDT-SWAP \
  --output-dir user_data/data/okx_profit_holdout
.venv/bin/python scripts/download_profit_funding.py
.venv/bin/python scripts/run_jev_profit_research.py \
  --env-file /absolute/path/to/local.env \
  --output-dir user_data/backtest_results/jev-profit-new-run
```

输出目录必须为空。行情API历史窗口可能随时间变化，复现已有结果优先使用清单哈希一致的本地数据。Jev缓存按请求、模型、提供器代码生成键；重复运行复用预测，新请求会调用真实API并产生费用。没有密钥或接口失败会报错，不伪造预测。

`training-grid.csv` 为全部192次训练结果；`validation-shortlist.json` 为12组开发复核；`portfolio-finalists.json` 为6组组合复核；`frozen-candidate.json` 保存诊断候选、阈值和结果；`manifest.json` 保存哈希及留出打开状态。无亏损时独立标签PF内部为无穷大、JSON为null，不用999假数字代替；仍须通过样本门槛。

因旧数据已经多轮使用，A/B都属于开发验证。只有基线或Jev通过全部开发门槛后，程序才加载预留行情执行跨币种历史评估；失败直接退出，不能将该退出解读为盈利成功。即使历史通过，也没有未来收益保证。

## 验证

```bash
.venv/bin/python -m pytest -q \
  tests/test_profit_signal_engine.py tests/test_run_jev_profit_research.py \
  tests/test_scalp_paths.py tests/test_scalp_backtester.py \
  tests/test_scalp_signal_engine.py tests/test_run_jev_scalp_research.py \
  tests/test_download_okx_scalp_data.py tests/test_okx_candles.py \
  tests/test_v2_backtester.py tests/test_v2_portfolio.py \
  tests/test_jev_provider.py tests/test_jev_research.py tests/test_run_jev_ma_research.py
```

本轮172项通过。提交只包含本轮研究文件与文档；不包含工作区原有观察台、三阶段筛选及streak-flip修改。
