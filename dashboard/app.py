"""FastAPI application for the local four-layer opportunity dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.scan_manager import (
    ScanManager,
    report_artifact_lock,
    run_production_scan,
)
from dashboard.view_model import load_dashboard


DEFAULT_REPORT_DIR = Path("/freqtrade/reports/okx-three-stage-screener")
_DASHBOARD_DIR = Path(__file__).resolve().parent
_INDEX_FILE = _DASHBOARD_DIR / "templates" / "index.html"
_STATIC_DIR = _DASHBOARD_DIR / "static"


def create_app(
    report_dir: Path = DEFAULT_REPORT_DIR,
    scan_manager: ScanManager | None = None,
) -> FastAPI:
    """Create the dashboard API with a fixed report location and scan command."""

    active = (
        scan_manager
        if scan_manager is not None
        else ScanManager(runner=run_production_scan)
    )
    report_path = Path(report_dir)
    app = FastAPI(title="OKX", docs_url=None, redoc_url=None)
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="dashboard-static",
    )

    @app.get("/", include_in_schema=False)
    def home_page() -> FileResponse:
        return FileResponse(
            _INDEX_FILE,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/dashboard")
    def dashboard_data() -> dict[str, object]:
        with report_artifact_lock():
            dashboard = load_dashboard(report_path)
        return {**dashboard, "scan": active.snapshot()}

    @app.get("/api/scan/status")
    def scan_status() -> dict[str, object]:
        return active.snapshot()

    @app.post("/api/scan", status_code=202)
    def start_scan() -> dict[str, object]:
        if not active.start():
            raise HTTPException(status_code=409, detail="scan_already_running")
        return active.snapshot()

    return app


app = create_app()
