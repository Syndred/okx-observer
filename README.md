# OKX 观察台

本地筛选 OKX USDT 永续合约的观察台。用 Docker 跑在你自己的电脑上，只读公开行情，**不需要 API Key**，也**不会下单**。

> 仅作筛选 · 不提供投资建议 · 不自动下单。  
> This is a local screener, not a trading bot and not investment advice.

支持 **macOS** 和 **Windows**。页面地址固定为 [http://127.0.0.1:8787](http://127.0.0.1:8787)。

## 它做什么

点一次「扫描」，会同时更新同一页上的四块内容：

| 区块 | 说明 |
|------|------|
| 涨跌幅榜 | 合资格合约的 24h 涨幅 / 跌幅各 9 个（3×3） |
| 连续涨跌 / 放量 | 连续阳线或阴线、放量启动、量能未放大的观察名单，各最多 9 个 |
| 重点看 | 从四层机会里挑出最多 9 张优先看的卡片 |
| 阶段 | 4H/15m 六均线缠绕 → 突破 → 第一次回踩的四层状态 |

卡片上的合约名可点到 Bitget 对应交易对（若该所也有）。颜色条和涨跌颜色表示方向，不写「多 / 空」字样。

仓库里还有历史研究脚本和失败回测报告。那些**没有通过**实盘门槛，不要当成可交易策略。

## 系统要求

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- 现代浏览器（Chrome / Edge / Safari / Firefox）
- macOS 13+，或 Windows 10/11（建议打开 Docker 的 WSL2 后端）
- 首次会拉取 `freqtradeorg/freqtrade:2026.5.1` 镜像，需要能访问 Docker Hub

不需要 Python、不需要交易所账户、不需要填 Key。

## 安装

```bash
git clone https://github.com/Syndred/okx-observer.git
cd okx-observer
```

也可以在 GitHub 页面点 **Code → Download ZIP**，解压后按下面的系统说明启动。

## macOS 使用

1. 安装并打开 [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)，等到菜单栏鲸鱼图标不再转圈。
2. 把仓库放到任意目录，例如 `~/okx-observer`。
3. 任选一种启动方式：
   - 双击 `启动观察台.app`（可拖到 Dock）
   - 双击 `启动观察台.command`（若提示无法打开：右键 → 打开）
   - 终端：`./scripts/launch_dashboard.sh`
4. 控制面板点「运行」。若 Docker 还没起来，脚本会先尝试打开 Docker Desktop，最多等约 90 秒。
5. 浏览器打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。
6. 点页面上的 **扫描**。全市场日线 + 4H + 15m 需要几分钟到十几分钟，进度会显示在页面上。
7. 用完点控制面板「停止」。默认会关掉观察台容器，并退出 Docker Desktop（避免后台占电）。若只想关观察台、保留 Docker，先在终端执行：

```bash
KEEP_DOCKER=1 ./scripts/stop_dashboard.sh
```

重新编译 Mac 控制面板（改过 `scripts/dashboard_gui.swift` 之后）：

```bash
./scripts/build_macos_app.sh
```

## Windows 使用

1. 安装 [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)。
2. 打开 Docker Desktop → Settings → General，勾选 **Use WSL 2 based engine**（推荐）。
3. 等到托盘里的 Docker 图标变为 Running。
4. 用 Git 克隆本仓库，或解压 ZIP。路径尽量不要有乱码，例如 `C:\Users\你的用户名\okx-observer`。
5. 双击 `启动观察台.bat`。若所在环境不方便使用中文文件名，双击 `start-observer.bat`。
6. 若 SmartScreen 提示「已阻止」，选 **更多信息 → 仍要运行**。这是未签名的本地脚本，不会联网安装其它软件。
7. 若 PowerShell 报执行策略错误，脚本已带 `-ExecutionPolicy Bypass`，一般双击 bat 即可。手动运行：

```powershell
cd C:\Users\你的用户名\okx-observer
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dashboard_gui.ps1
```

8. 控制面板点「运行」，浏览器会打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。
9. 点页面上的 **扫描**，等进度走完。
10. 用完点「停止」。默认会停止观察台并退出 Docker Desktop。若要保留 Docker：

```powershell
$env:KEEP_DOCKER = "1"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_dashboard.ps1
```

命令行启动（不要控制面板窗口）也可以：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\launch_dashboard.ps1
```

已安装 Git Bash / WSL 时，也可以用和 macOS 相同的 `./scripts/launch_dashboard.sh`。

## 页面怎么用

1. 第一次打开若还没有报告，会看到「等待第一份扫描报告」。点 **扫描**。
2. 扫描进行中不要关浏览器；关掉页面不会停扫描，但看不到进度。
3. 扫描失败时会保留上一份卡片，不会把名单抹掉。
4. 硬刷新：macOS 用 `Cmd+Shift+R`，Windows 用 `Ctrl+Shift+R`。
5. 已在 `127.0.0.1:8787` 就绪时，再点「运行」只会打开页面，不会再起一个容器。

## 命令行速查

在仓库根目录：

```bash
# 启动（已就绪则只打开浏览器）
./scripts/launch_dashboard.sh          # macOS / WSL / Git Bash
# Windows: scripts\launch_dashboard.ps1

# 停止观察台
./scripts/stop_dashboard.sh
# Windows: scripts\stop_dashboard.ps1

# 只启动网页容器（Docker 已在运行时）
docker compose up -d --no-deps dashboard

# 看容器日志
docker compose logs -f --tail 80 dashboard
```

容器名固定为 `okx-dashboard`，只监听本机 `127.0.0.1:8787`，不会暴露到局域网。

## 常见问题

**点了运行没反应 / 提示找不到 docker**  
先打开 Docker Desktop，等到引擎 Running。Mac 上从 Finder 双击时，脚本会自动补上 Docker 的 PATH；若仍失败，用终端跑 `./scripts/launch_dashboard.sh` 看完整报错。

**8787 已被占用**  
本机已经有别的程序占用该端口。关掉旧的观察台，或结束占用 8787 的进程后再启动。

**扫描很慢**  
正常。要拉全市场 live USDT 永续的日线、4H、15m。保持 Docker 在跑、网络能访问 OKX 公开接口。不要同时开两次扫描。

**Windows 双击 bat 一闪而过**  
在资源管理器地址栏输入 `cmd` 回车，再执行 `启动观察台.bat`，就能看到报错。常见原因是没装 Docker Desktop，或引擎还在启动。

**停止之后其它 Docker 项目也没了**  
这是当前默认行为：停止观察台时会退出 Docker Desktop。下次启动 Docker Desktop 再开那些项目即可。若不想退出 Docker，用上面的 `KEEP_DOCKER=1`。

**要不要 API Key？会不会下单？**  
不要 Key。观察台只用公开行情。页面和启动器都写了「不自动下单」。本仓库也没有实盘下单配置。

**能当量化实盘用吗？**  
不能。历史研究路径没有通过既定门槛（样本外利润因子、回撤、交易数等）。不要把筛选结果当成买卖建议。

## 目录说明

独立的 **Jev 均线策略历史评估** 已提供命令行入口，比较原始候选与 AI 概率筛选后的胜率、收益、回撤和概率校准。该研究需要 TypeSafe API Key，观察台本身仍不需要。用法与规则见 [Jev 研究说明](docs/research/JEV_MA_RESEARCH.md)。

真实 5 分钟行情、15/30/60 分钟持仓和训练/验证/留出评估见 [超短线研究说明](docs/research/JEV_SCALP_RESEARCH.md)。目标为扣除成本后的胜率超过 50%，是否达标以冻结留出报告为准。

```text
dashboard/                 观察台网页与 API
scripts/launch_dashboard.* 跨平台启动
scripts/stop_dashboard.*   跨平台停止
scripts/dashboard_gui.swift  macOS 控制面板源码
scripts/dashboard_gui.ps1    Windows 控制面板
启动观察台.app / .command    macOS 入口
启动观察台.bat / start-observer.bat  Windows 入口
reports/okx-three-stage-screener/   最新扫描结果
user_data/strategy_lib/    筛选逻辑（只读计算，不下单）
```

## 免责声明

本软件按「现状」提供，用于个人研究与形态筛选。作者不对任何盈亏负责。使用即表示你理解加密货币合约有高风险，可能损失全部本金。完整条款见 [LICENSE](LICENSE)（GPL-3.0，因本项目在 Freqtrade 镜像中运行并包含相关脚本）。

## English

Local OKX USDT-perpetual observation dashboard. It runs in Docker on your machine, reads public market data, needs **no API keys**, and **does not place orders**.

- macOS: double-click `启动观察台.app` or `启动观察台.command`
- Windows: double-click `启动观察台.bat` or `start-observer.bat`
- Open [http://127.0.0.1:8787](http://127.0.0.1:8787) and click **扫描** (Scan)

Not investment advice. Historical research in this repository did not pass live-trading gates; do not treat it as a working strategy.
