# 台式机 GPT 接手文档：Crypto / Jev 超短线研究

交接日期：2026-09-27。请将本文件完整交给台式机 GPT。用户要求把研究迁到台式机继续，Mac 停止本次研究计算。下述路径除注明 Mac 绝对路径外，均相对于仓库根目录。

## 给接手 GPT 的任务

先完成环境、代码、数据和 Jev 配置校验，再从已冻结的长历史实验续跑。目标是找到扣除手续费、滑点和资金费后，净胜率至少50%、收益和压力测试均达标的超短线候选。不要承诺能迭代到盈利，不为达标降低门槛。保留失败实验、中文交接、代码提交记录。不得下实盘订单。

**当前没有已证实盈利版本。当前长历史研究尚未开始 Jev 评分，行情仍待续传，不能把代码完成写成策略成功。**

## 1. 代码获取

- 仓库：`https://github.com/Syndred/okx-observer`
- SSH：`git@github.com:Syndred/okx-observer.git`
- 分支：`main`
- 本轮研究实现提交：`9263be9`，或包含它的更新 main。本文及交接进度另有文档提交。
- GitHub 访问使用用户在台式机自己的账户认证。不要把 Mac 的 SSH 私钥拷进项目。

```bash
git clone https://github.com/Syndred/okx-observer.git
cd okx-observer
git fetch origin
git pull --ff-only origin main
git merge-base --is-ancestor 9263be9 HEAD
git log -5 --oneline
```

已有仓库先 `git status --short`，有未提交修改先检查并保留，再更新，禁止强制 reset。新研究可从最新 main 建 `codex/desktop-jev-research` 分支。Mac 存在另一轮观察台、筛选器、streak-flip 等未提交修改，它们不属于本次迁移，未混入 main；接手请以已提交版本为准。

## 2. 环境

建议 Windows 11 + WSL2 Ubuntu 24.04，或直接 Linux。原生 Windows PowerShell 也可。硬件建议8核以上、16GB内存起步，32GB更宽裕，预留10GB以上空间；这些是规划建议，不是实测最低配置。不需要 GPU，Jev 在远端执行。当前这轮研究不要求安装完整 JevPlay、Node、Docker、Freqtrade，也不需要交易所账户密钥。

安装 Git、Python 3.11（建议）或3.12、venv/pip。Mac 现有实测是 Python3.9，81项针对性测试通过，有 LibreSSL 警告；换机必须重新测试，不能直接沿用 Mac 的 `.venv`。

WSL/Linux：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-research-test.txt
```

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-research-test.txt
```

后续命令用已激活环境的 `python`；PowerShell不激活时替换为 `.\.venv\Scripts\python.exe`。主要依赖 pandas、numpy、pyarrow、requests、pytest；Mac 实测版本 pandas2.3.3 / numpy2.0.2 / pyarrow21.0.0 / requests2.32.5 / pytest8.4.2，仓库 requirements 是兼容范围，并非完整锁文件。先验证依赖再做大批实验。

网络须能访问 GitHub、`www.okx.com`、`static.okx.com`、`api.typesafe.ai` 和 pip 包源。代理按台式机实际情况配置，勿照搬 Mac 本地端口。不要关闭 TLS 校验。

## 3. 数据迁移（必须连同文档一起传）

Git 不包含 `user_data/data/`、`user_data/backtest_results/`、`work/`。只拉代码不能接上本次进度。

Mac 已生成独立文件夹：`/Users/syndred/Desktop/crypto-desktop-handoff-20260927/`。

请传其中：

- 本文档。
- `crypto-research-data-20260927.tar.gz`（38,821,701字节，约37MiB）。
- `SHA256SUMS.txt` 和 `DATA_MANIFEST.json`。

压缩包SHA256：`a1ecfb0fde2c21a713fa4cfa127cfa03228215e7a91899618a7920fd419a1e0e`。

包括原始/扩展行情、未完成长历史下载断点 `.cache`、完整长历史资金费、官方ZIP与来源缓存、Jev预测缓存、相关原始研究结果，以及本地旧交接。**不含密钥、虚拟环境、Git目录或无关未提交代码。** 预留8币数据也被完整拷贝，但没有进行其策略绩效评估；仅迁移字节不代表开启验收。

在仓库根目录，把压缩包放在此处后执行：

```bash
python -c "import hashlib; from pathlib import Path; p=Path('crypto-research-data-20260927.tar.gz'); assert hashlib.sha256(p.read_bytes()).hexdigest()=='a1ecfb0fde2c21a713fa4cfa127cfa03228215e7a91899618a7920fd419a1e0e'; print('archive OK')"
tar -xzf crypto-research-data-20260927.tar.gz
python -c "import json,hashlib; from pathlib import Path; m=json.loads(Path('work/desktop-handoff/DATA_MANIFEST.json').read_text()); bad=[r['path'] for r in m['files'] if not Path(r['path']).is_file() or hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()!=r['sha256']]; assert not bad,bad; print('all files verified:',len(m['files']))"
```

在新克隆中解压；若已有同名研究文件，先保留，不覆盖较新成果。包内路径相对仓库，可跨系统迁移。部分历史manifest记录Mac绝对来源路径，属于历史证据，不要批量改写已签哈希文件；实际入口使用当前相对目录。包内PROJECT_CONTEXT可能是历史状态，**以本文和最新PROJECT_PROGRESS为准**。

若缺少数据包，不要立刻重抓全部数据，尤其已超出REST资金费历史范围的旧数据；先让用户补传此包。

## 4. Jev 配置

- Endpoint：`https://api.typesafe.ai/v1/systemone`
- 模型精确值：`jev-1.13.0`
- 客户端：`user_data/strategy_lib/jev_provider.py`，复用已核验的 JevPlay System One 请求格式。
- 首选变量：`TYPESAFE_API_KEY`，兼容 `JEV_API_KEY`；前者优先。只设置一个，避免误用旧值。
- 默认超时30秒，研究入口评分4线程。响应必须是合法概率，不用本地猜测代替失败请求。

密钥当前保存在 Mac 的 `/Users/syndred/Desktop/projects/JevPlay/.env.development`。**请用户通过自己的安全方式，仅迁移其中 TYPESAFE_API_KEY 或 JEV_API_KEY 那一项**；无需复制整个JevPlay环境文件，里面可能有无关凭证。本文和数据包不提供明文密钥，不要提交或输出它。

台式机仓库根目录新建已被git忽略的 `.env.jev`，内容：

```dotenv
TYPESAFE_API_KEY=用户单独提供的真实密钥
```

这是占位符，运行前必须替换。Linux可 `chmod 600 .env.jev`。研究命令显式带 `--env-file .env.jev`；不要把密钥写进命令行、报告或截图。已有环境变量会优先于env文件，若认证失败先检查冲突但不要打印值。

以下只校验能加载密钥，**不表示已验证远端认证或余额**：

```bash
python -c "from pathlib import Path; from scripts.run_jev_ma_research import load_key_file; from user_data.strategy_lib.jev_provider import JevClient; load_key_file(Path('.env.jev')); c=JevClient(); print('configured model:',c.model)"
```

真实评分会产生API费用，保留 `user_data/backtest_results/jev-passive-cache` 可复用相同请求。缓存按完整状态、模型、provider代码哈希匹配；不要修改缓存使其伪命中。遇到额度、认证或网络失败记录原因，不得当作评分成功。

## 5. 当前准确进度

- 旧超短线实验曾出现57.98%胜率，但100U降至86.91U，PF0.614，失败。
- 被动限价候选59：原训练PF1.423，开发A胜率44% / PF0.910，失败。
- 上轮扩展训练：253订单评分（192缓存+61真实调用）。阈值0.35只有54笔，胜率62.96%，样本不足80；阈值0.25有107笔但胜率48.60%。六阈值全部未通过，没有进入A/B。
- 已冻结新长历史协议 `docs/research/JEV_LONG_HISTORY_PROTOCOL.md`，实现提交9263be9。新一轮尚未评分。
- `okx_scalp_extended`：207360根5m K、2160条资金费，完整。
- `okx_scalp_long`：目前只有已完成的资金费和manifest，原8币各636条，共5088；标准00/08/16 UTC结算无缺失、无重复；每币官方档案与REST重叠8条，差值0。长行情尚未拼接。
- `okx_scalp_prefix_20260226`：2月26日至6月28日下载被用户迁移需求中止。BTC/ETH/SOL已各35136根且缺失0，其他币保存了断点。目录存在不等于完整；恢复同命令后必须检查最终manifest。
- Mac下载PID14287已终止；资金费任务已正常结束；没有继续运行本次Jev回归。

## 6. 接手执行顺序

先验证代码和数据，再运行以下81项定向测试（Mac已通过，台式机必须重跑）：

```bash
python -m pytest -q tests/test_prepare_long_funding.py tests/test_prepare_long_training.py tests/test_jev_passive_filter.py tests/test_passive_scalp_paths.py tests/test_jev_provider.py
```

恢复下载，**日期和输出目录保持一致才能使用断点**：

```bash
python scripts/download_okx_scalp_data.py --start 2026-02-26T00:00:00Z --end 2026-06-28T00:00:00Z --output-dir user_data/data/okx_scalp_prefix_20260226 --workers 3
```

预期8币各35136根、合计281088根，manifest `complete=true`，所有质量项及缺K为0。资金费已有完整文件，不需联网重抓；若校验失败，先查明原因，修复入口为 `python scripts/prepare_long_funding.py`，会复用 `work/okx-long-funding-cache`。

拼接后预期8币共488448根K、5088条资金费：

```bash
python scripts/prepare_long_training.py
```

必须读取 `user_data/data/okx_scalp_long/long-manifest.json` 确认 `complete=true` 和来源哈希；脚本不允许旧数据被覆盖。完成后首次运行：

```bash
python scripts/run_jev_passive_filter.py --long-history --data-dir user_data/data/okx_scalp_long --env-file .env.jev --output-dir user_data/backtest_results/jev-passive-long-desktop-20260927
```

输出目录必须为空。中断重跑用新的输出目录后缀如 `-retry1`，保留旧失败manifest，缓存仍自动复用。开始前记录 `git rev-parse HEAD`、依赖版本和数据manifest；终端日志保存到本机文件。不要同时启动两个相同研究写同一缓存/结果目录。

## 7. 验收与下一轮策略工作

固定原8币 BTC/ETH/SOL/XRP/DOGE/ADA/LINK/AVAX。候选59：双EMA9/21的成本感知breakout，stop_atr=3、min_atr_pct=0.004、min_body_atr=0；有利方向偏移0.25ATR限价，2R目标，最长60分钟。只下一根5m可成交，跳空变成吃单则取消；穿价1bp假设全成，同入场K只允许止损不允许止盈，后续同K止损优先。maker0.0002、退出taker0.0005加滑点0.0005为研究假设，纳入真实资金费。执行细节以协议和代码为准。

训练2026-03-01至06-01，开发A06-01至07-15，开发B07-15至09-26，均UTC左闭右开。候选曾利用7/8月信息选择，重新分段后这些**全部是开发数据**，不可称独立样本外。

训练仅选六阈值0.15/0.25/0.35/0.45/0.55/0.65，各情景至少80成交，常规净胜率≥50%、PF≥1.2、净收益均值>0；两压力场景PF≥1、均值≥0。压力分别为同成交条件成本翻倍，以及更严格2bp穿价且成本翻倍。训练失败就停止A/B；A失败就停止B。A/B各至少30成交，相同收益/胜率门槛，A+B常规至少100。

若全部标签通过，仍须实现并核验组合资金约束、并发仓位、最大回撤和执行敏感性。此前总体验收还包含100U组合正收益、最大回撤≤20%、压力非负及足够预留样本（至少100成交）；需要明确报告而非只报独立标签PF。随后才用 `okx_profit_holdout` 中 SUI/NEAR/UNI/AAVE/LTC/BNB/DOT/ATOM 的预留数据验证，禁止提前拿其收益调参。OHLC假定挂单成交不能证明真实排队/部分成交表现，最后仍需前向模拟证据。

若新长历史实验失败，保存失败原因，基于亏损归因提出下一种有解释的策略改动，先冻结新协议，再实验，不反复改阈值包装成功。用户目标允许继续策略迭代，未授权实盘。

## 8. 台式机如何利用更多算力

先完成此固定实验，保证可复现。当前历史下载最多3线程且全局限速，Jev固定4并发；更多CPU不等于远端配额更多，先保持默认。CPU密集的候选回测以后可按候选分进程，工作进程各写独立结果目录，由主进程合并；逐步增到物理核心数减2，并观察内存。不让多个任务同时改协议、开预留、写相同缓存。多代理只分派清晰独立模块，主代理负责审查。

最新证据入口：`PROJECT_PROGRESS.md`；`reports/jev-passive-extended-20260927/PROGRESS_REPORT.md`；`reports/jev-passive-filter-20260927/PROGRESS_REPORT.md`；`docs/research/JEV_LONG_HISTORY_PROTOCOL.md`。不要从历史Binance/Freqtrade任务重新开始。

## 9. 接手完成应向用户报告

一次性报告拉取的提交号、环境与测试、数据哈希/完整性、Jev实际调用状态、当前实验阶段和结果。后续每轮记录所有尝试，中文更新进度，定向测试和自审后commit/push，区分代码已提交、已推送、回归达标三个状态。遇到确实缺少密钥/权限/数据，一次性列出缺项，不重复询问已明确的范围。
