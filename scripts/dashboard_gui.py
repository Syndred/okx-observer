#!/usr/bin/env python3
"""Helpers and fallback opener for the native observation-dashboard panel."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8787")
HEALTHZ_URL = f"{DASHBOARD_URL.rstrip('/')}/healthz"


def is_healthy(url: str = HEALTHZ_URL, runner=subprocess.run) -> bool:
    result = runner(
        ["curl", "--fail", "--silent", "--show-error", "--max-time", "2", url],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def start_dashboard(project_root: Path = PROJECT_ROOT, runner=subprocess.run):
    return runner(
        ["bash", str(project_root / "scripts/launch_dashboard.sh")],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        check=False,
    )


def stop_dashboard(project_root: Path = PROJECT_ROOT, runner=subprocess.run):
    return runner(
        ["bash", str(project_root / "scripts/stop_dashboard.sh")],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        check=False,
    )


def status_label(healthy: bool) -> str:
    return "运行中" if healthy else "未启动"


def main() -> int:
    if sys.platform == "win32":
        script = PROJECT_ROOT / "scripts" / "dashboard_gui.ps1"
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            check=False,
        ).returncode
    app = PROJECT_ROOT / "启动观察台.app"
    subprocess.run(["open", str(app)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
