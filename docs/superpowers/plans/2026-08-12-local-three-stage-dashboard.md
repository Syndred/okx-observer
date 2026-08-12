# 本地六均线三阶段仪表盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个一键启动的本地三阶段筛选网页，支持北京时间显示、Bitget跳转、单任务实时重扫和失败保留旧结果。

**Architecture:** 在现有 Freqtrade Docker 镜像中复用 FastAPI、Uvicorn 和 Jinja2。后端只读取三阶段 CSV/manifest，并通过固定命令启动现有扫描脚本；前端按阶段渲染卡片并轮询扫描状态。Docker 端口只映射到本机 `127.0.0.1:8787`。

**Tech Stack:** Python 3.14、FastAPI 0.136.1、Uvicorn 0.47.0、Jinja2 3.1.6、pandas、原生 JavaScript/CSS、Docker Compose、unittest。

## Global Constraints

- 不新增第三方依赖，复用 Freqtrade 镜像现有包。
- 服务仅通过 `127.0.0.1:8787` 暴露到宿主机。
- 后端计算仍使用 UTC；所有用户可见事件时间由后端转成 `Asia/Shanghai`。
- 页面只能触发固定三阶段扫描，不接受任意命令、路径或策略参数。
- 不连接 Bitget/OKX 私有账户，不读取密钥，不下单。
- Bitget 链接只用于导航，链接可打开不等于该合约可交易。
- 扫描失败不得删除或覆盖上一次成功结果。
- 保留现有 Markdown、CSV、manifest 和旧 `reports/okx-screener/`。

---

### Task 1: 仪表盘数据模型、北京时间和 Bitget 链接

**Files:**
- Create: `dashboard/__init__.py`
- Create: `dashboard/view_model.py`
- Create: `tests/test_three_stage_dashboard.py`

**Interfaces:**
- Consumes: `reports/okx-three-stage-screener/latest.csv` 与 `run-manifest.json`。
- Produces: `bitget_url(instrument: str) -> str`。
- Produces: `format_shanghai(value: object) -> str | None`。
- Produces: `load_dashboard(report_dir: Path) -> dict[str, object]`，返回 `summary`、`stages`、`near_stage`、`diagnostics`。

- [ ] **Step 1: 写 Bitget 链接与北京时间失败测试**

```python
class DashboardViewModelTests(unittest.TestCase):
    def test_bitget_url_maps_okx_swap_to_usdt_contract(self):
        self.assertEqual(
            bitget_url("LINK-USDT-SWAP"),
            "https://www.bitget.com/zh-CN/futures/usdt/LINKUSDT",
        )

    def test_format_shanghai_converts_utc_without_using_host_timezone(self):
        self.assertEqual(
            format_shanghai("2026-08-12T00:30:00+00:00"),
            "2026-08-12 08:30",
        )
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest tests.test_three_stage_dashboard.DashboardViewModelTests -v
```

Expected: FAIL，`dashboard.view_model` 尚不存在。

- [ ] **Step 3: 实现纯函数链接与时区转换**

```python
BITGET_USDT_BASE = "https://www.bitget.com/zh-CN/futures/usdt"
SHANGHAI = ZoneInfo("Asia/Shanghai")

def bitget_url(instrument: str) -> str:
    base = instrument.removesuffix("-USDT-SWAP")
    safe = "".join(char for char in base.upper() if char.isalnum())
    return f"{BITGET_USDT_BASE}/{safe}USDT"

def format_shanghai(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.Timestamp(value)
    parsed = parsed.tz_localize("UTC") if parsed.tzinfo is None else parsed.tz_convert("UTC")
    return parsed.tz_convert(SHANGHAI).strftime("%Y-%m-%d %H:%M")
```

- [ ] **Step 4: 写报告分组与旧结果缺失测试**

```python
def test_load_dashboard_groups_formal_and_near_states(self):
    write_report_fixture(
        self.temp_dir,
        rows=[
            {"instrument": "A-USDT-SWAP", "role": "trade", "stage": "entry_confirmed", "reason": "first_pullback_held"},
            {"instrument": "B-USDT-SWAP", "role": "trade", "stage": "none", "reason": "fifteen_minute_coil_not_ready"},
            {"instrument": "BTC-USDT-SWAP", "role": "reference", "stage": "none", "reason": "reference_only"},
        ],
    )
    result = load_dashboard(self.temp_dir)
    self.assertEqual([item["instrument"] for item in result["stages"]["entry_confirmed"]], ["A-USDT-SWAP"])
    self.assertEqual([item["instrument"] for item in result["near_stage"]], ["B-USDT-SWAP"])
    self.assertNotIn("BTC-USDT-SWAP", str(result["stages"]))

def test_missing_report_returns_first_scan_empty_state(self):
    result = load_dashboard(self.temp_dir)
    self.assertEqual(result["summary"]["status"], "not_scanned")
    self.assertEqual(result["stages"]["entry_confirmed"], [])
```

- [ ] **Step 5: 实现报告读取和稳定分组**

`load_dashboard` 必须：

1. 先验证 CSV 与 manifest 均存在；任一缺失时返回 `status=not_scanned`。
2. 使用 `json.loads` 与 `pandas.read_csv` 读取正式产物，不修改文件。
3. 只把 `role=trade` 的四个正式 stage 放进 `stages`。
4. 只把 `reason` 属于 `fifteen_minute_coil_not_ready`、`fifteen_minute_setup_expired` 的交易合约放进 `near_stage`。
5. 给每个合约增加 `bitget_url`，并把 `*_at` 时间字段转为北京时间字符串。
6. 按 `(四阶段顺序, 四小时分+十五分钟分降序, instrument升序)` 稳定排序。
7. 从 manifest 输出 `retrieved_at_shanghai`、`live_usdt_swaps`、`eligible_trade_contracts`、`error_count`。

- [ ] **Step 6: 运行 Task 1 测试确认 GREEN**

Run: 同 Step 2。Expected: Task 1 全部 PASS。

- [ ] **Step 7: 提交 Task 1**

```bash
git add dashboard/__init__.py dashboard/view_model.py tests/test_three_stage_dashboard.py
git commit -m "feat: add dashboard report view model"
```

---

### Task 2: FastAPI 页面接口和单任务扫描管理器

**Files:**
- Create: `dashboard/scan_manager.py`
- Create: `dashboard/app.py`
- Modify: `tests/test_three_stage_dashboard.py`

**Interfaces:**
- Consumes: Task 1 `load_dashboard(report_dir)`。
- Produces: `ScanSnapshot`，字段为 `state`、`stage`、`started_at`、`finished_at`、`error`、`log_tail`。
- Produces: `ScanManager.start() -> bool`、`ScanManager.snapshot() -> dict[str, object]`。
- Produces: FastAPI routes `GET /healthz`、`GET /api/dashboard`、`GET /api/scan/status`、`POST /api/scan`。

- [ ] **Step 1: 写扫描锁、进度和失败保留测试**

```python
def test_scan_manager_allows_only_one_active_scan(self):
    release = threading.Event()
    manager = ScanManager(runner=lambda progress: release.wait(timeout=2))
    self.assertTrue(manager.start())
    self.assertFalse(manager.start())
    release.set()
    manager.wait(timeout=2)
    self.assertEqual(manager.snapshot()["state"], "succeeded")

def test_failed_scan_reports_error_without_removing_report(self):
    report = self.temp_dir / "latest.csv"
    report.write_text("instrument,stage\nOLD-USDT-SWAP,none\n", encoding="utf-8")
    manager = ScanManager(runner=lambda progress: (_ for _ in ()).throw(RuntimeError("network failed")))
    manager.start()
    manager.wait(timeout=2)
    self.assertEqual(manager.snapshot()["state"], "failed")
    self.assertTrue(report.exists())
```

- [ ] **Step 2: 运行扫描管理测试确认 RED**

Run:

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest tests.test_three_stage_dashboard.ScanManagerTests -v
```

Expected: FAIL，`dashboard.scan_manager` 尚不存在。

- [ ] **Step 3: 实现线程安全扫描管理器**

`ScanManager` 使用 `threading.Lock` 保护状态，并用单个 daemon thread 执行 runner。生产 runner 固定执行：

```python
command = [
    sys.executable,
    "/freqtrade/scripts/scan_okx_three_stage.py",
    "--output-dir",
    "/freqtrade/reports/okx-three-stage-screener",
    "--workers",
    "6",
    "--requests-per-second",
    "10",
]
```

通过 `subprocess.Popen(..., stdout=PIPE, stderr=STDOUT, text=True)` 逐行读取输出；看到 `[1/3]`、`[2/3]`、`[3/3]` 时分别更新 `daily`、`four_hour`、`fifteen_minute`。仅保留最后40行日志。退出码非0时状态为 `failed`，错误只包含清理过的最后5行日志。

- [ ] **Step 4: 写 FastAPI 接口测试**

```python
def test_api_starts_scan_and_rejects_duplicate(self):
    app = create_app(report_dir=self.temp_dir, scan_manager=self.manager)
    with TestClient(app) as client:
        self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
        self.assertEqual(client.post("/api/scan").status_code, 202)
        self.assertEqual(client.post("/api/scan").status_code, 409)

def test_dashboard_api_returns_previous_data_while_scan_failed(self):
    app = create_app(report_dir=self.temp_dir, scan_manager=self.failed_manager)
    with TestClient(app) as client:
        payload = client.get("/api/dashboard").json()
    self.assertEqual(payload["stages"]["entry_confirmed"][0]["instrument"], "A-USDT-SWAP")
    self.assertEqual(payload["scan"]["state"], "failed")
```

- [ ] **Step 5: 实现 FastAPI 工厂与接口**

```python
def create_app(report_dir: Path = DEFAULT_REPORT_DIR, scan_manager: ScanManager | None = None) -> FastAPI:
    active = scan_manager or ScanManager(runner=run_production_scan)
    app = FastAPI(title="六均线三阶段筛选器", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/dashboard")
    def dashboard_data() -> dict[str, object]:
        return {**load_dashboard(report_dir), "scan": active.snapshot()}

    @app.post("/api/scan", status_code=202)
    def start_scan(response: Response) -> dict[str, object]:
        if not active.start():
            raise HTTPException(status_code=409, detail="scan_already_running")
        return active.snapshot()
```

`GET /api/scan/status` 只返回 `active.snapshot()`；所有接口不接受命令、目录或扫描参数。

- [ ] **Step 6: 运行 Task 2 测试确认 GREEN**

Run: 同 Step 2。Expected: Task 1 与 Task 2 全部 PASS。

- [ ] **Step 7: 提交 Task 2**

```bash
git add dashboard/scan_manager.py dashboard/app.py tests/test_three_stage_dashboard.py
git commit -m "feat: add local dashboard scan API"
```

---

### Task 3: 交易终端页面、Docker服务和一键打开

**Files:**
- Create: `dashboard/templates/index.html`
- Create: `dashboard/static/dashboard.css`
- Create: `dashboard/static/dashboard.js`
- Create: `scripts/open_three_stage_dashboard.sh`
- Modify: `dashboard/app.py`
- Modify: `docker-compose.yml`
- Modify: `tests/test_three_stage_dashboard.py`
- Modify: `reports/okx-three-stage-screener/README.md`
- Modify: `PROJECT_CONTEXT.md`

**Interfaces:**
- Consumes: `GET /api/dashboard`、`POST /api/scan`、`GET /api/scan/status`。
- Produces: `GET /` 的本地仪表盘页面。
- Produces: `./scripts/open_three_stage_dashboard.sh` 一键启动并打开浏览器。

- [ ] **Step 1: 写首页、静态资源和Compose边界失败测试**

```python
def test_home_page_contains_dashboard_mount_points(self):
    app = create_app(report_dir=self.temp_dir, scan_manager=self.manager)
    with TestClient(app) as client:
        response = client.get("/")
    self.assertEqual(response.status_code, 200)
    self.assertIn("六均线三阶段筛选器", response.text)
    self.assertIn('id="scan-now"', response.text)
    self.assertIn('/static/dashboard.css', response.text)

def test_compose_dashboard_is_bound_to_loopback_only(self):
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["dashboard"]
    self.assertIn("127.0.0.1:8787:8787", service["ports"])
```

- [ ] **Step 2: 运行 Task 3 测试确认 RED**

Run:

```bash
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest tests.test_three_stage_dashboard -v
```

Expected: 首页或 dashboard Compose service 断言失败。

- [ ] **Step 3: 实现HTML语义结构**

`index.html` 必须包含：顶部品牌与风险标签、北京时间和扫描摘要、扫描按钮、四阶段容器、观察容器、错误状态、空状态和移动端 viewport。卡片内容由 `dashboard.js` 使用 `textContent` 创建，禁止将CSV字段拼入 `innerHTML`。

- [ ] **Step 4: 实现深色交易终端样式**

`dashboard.css` 使用 CSS variables 定义：

```css
:root {
  --bg: #071018;
  --panel: rgba(14, 27, 39, 0.86);
  --line: rgba(130, 168, 190, 0.18);
  --cyan: #47d7e8;
  --green: #40e0a1;
  --violet: #a882ff;
  --amber: #ffbd62;
  --danger: #ff6b7a;
  --text: #eaf7ff;
  --muted: #86a4b5;
}
```

桌面使用12列网格，阶段区至少占6列；候选卡包含左侧方向色条、指标网格和底部Bitget按钮。`@media (max-width: 760px)` 改为单列并隐藏次要区间字段，但保留阶段、方向、时间和跳转按钮。尊重 `prefers-reduced-motion`。

- [ ] **Step 5: 实现前端数据刷新和扫描轮询**

`dashboard.js` 必须：

1. 页面加载调用 `/api/dashboard`。
2. 使用 `Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai"})` 每秒更新当前北京时间；事件时间只显示后端字符串。
3. 使用DOM API和 `textContent` 渲染卡片。
4. 点击按钮 `POST /api/scan`；202或409都进入轮询。
5. 每2秒轮询 `/api/scan/status`；`running` 时更新三阶段进度，`succeeded` 时重新加载 dashboard，`failed` 时显示错误并继续保留现有卡片。
6. Bitget 链接设置 `target="_blank" rel="noopener noreferrer"`。

- [ ] **Step 6: 新增 dashboard Compose service**

```yaml
  dashboard:
    image: freqtradeorg/freqtrade:2026.5.1
    volumes:
      - "./dashboard:/freqtrade/dashboard:ro"
      - "./user_data:/freqtrade/user_data"
      - "./scripts:/freqtrade/scripts:ro"
      - "./config:/freqtrade/config:ro"
      - "./reports:/freqtrade/reports"
    working_dir: /freqtrade
    environment:
      PYTHONPATH: /freqtrade
      TZ: Asia/Shanghai
    ports:
      - "127.0.0.1:8787:8787"
    entrypoint: ["python", "-m", "uvicorn"]
    command: ["dashboard.app:app", "--host", "0.0.0.0", "--port", "8787"]
```

- [ ] **Step 7: 实现一键启动脚本**

`open_three_stage_dashboard.sh` 必须：进入项目根目录，执行 `docker compose up -d dashboard`，最多等待30次 `/healthz`，成功后执行 `open http://127.0.0.1:8787`；失败时输出 `docker compose logs --tail 80 dashboard` 并返回非0。脚本不得停止或删除其他Compose服务。

- [ ] **Step 8: 更新README与中文交接**

文档写明：

```bash
./scripts/open_three_stage_dashboard.sh
```

浏览地址 `http://127.0.0.1:8787`，扫描通常需要约2–3分钟；页面的 Bitget 按钮只负责打开对应币种合约；停止服务使用 `docker compose stop dashboard`。

- [ ] **Step 9: 定向测试、浏览器验收和全量回归**

Run:

```bash
python3 -m py_compile dashboard/app.py dashboard/view_model.py dashboard/scan_manager.py
git diff --check
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest tests.test_three_stage_dashboard -v
docker compose up -d dashboard
curl --fail http://127.0.0.1:8787/healthz
docker compose run --rm --no-deps --entrypoint python freqtrade \
  -m unittest discover -s tests -q
```

Expected: 所有命令 exit 0；仪表盘显示北京时间与现有11个观察项，LINK按钮为正确Bitget URL；点击扫描时按钮禁用，重复请求不启动第二个任务，完成后页面刷新。

- [ ] **Step 10: 提交 Task 3**

```bash
git add dashboard/templates/index.html dashboard/static/dashboard.css \
  dashboard/static/dashboard.js dashboard/app.py docker-compose.yml \
  scripts/open_three_stage_dashboard.sh tests/test_three_stage_dashboard.py \
  reports/okx-three-stage-screener/README.md PROJECT_CONTEXT.md
git commit -m "feat: add local three-stage trading dashboard"
```
