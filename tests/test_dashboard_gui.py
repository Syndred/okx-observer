from __future__ import annotations

from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

from scripts.dashboard_gui import (
    is_healthy,
    start_dashboard,
    status_label,
    stop_dashboard,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DashboardGuiTests(unittest.TestCase):
    def test_status_label_is_short_chinese(self) -> None:
        self.assertEqual(status_label(True), "运行中")
        self.assertEqual(status_label(False), "未启动")

    def test_is_healthy_uses_healthz(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        self.assertTrue(is_healthy(runner=runner))
        command = runner.call_args.args[0]
        self.assertEqual(command[0], "curl")
        self.assertIn("http://127.0.0.1:8787/healthz", command)

    def test_start_uses_existing_singleton_launcher(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        start_dashboard(project_root=PROJECT_ROOT, runner=runner)
        command = runner.call_args.args[0]
        self.assertEqual(command[:2], ["bash", str(PROJECT_ROOT / "scripts/launch_dashboard.sh")])

    def test_stop_only_calls_dashboard_stop_script(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        stop_dashboard(project_root=PROJECT_ROOT, runner=runner)
        command = runner.call_args.args[0]
        self.assertEqual(command[:2], ["bash", str(PROJECT_ROOT / "scripts/stop_dashboard.sh")])
        script = (PROJECT_ROOT / "scripts/stop_dashboard.sh").read_text(encoding="utf-8")
        self.assertIn("docker compose stop", script)
        self.assertIn("dashboard_path.sh", script)
        self.assertIn("/usr/local/bin", (PROJECT_ROOT / "scripts/dashboard_path.sh").read_text(encoding="utf-8"))
        self.assertIn("okx-dashboard", script)
        self.assertIn('quit app "Docker"', script)
        self.assertIn("KEEP_DOCKER", script)
        self.assertIn("powershell.exe", script)
        self.assertNotIn("compose stop freqtrade", script)
        self.assertNotIn("docker stop freqtrade", script)

    def test_windows_entry_uses_powershell_control_panel(self) -> None:
        bat = (PROJECT_ROOT / "启动观察台.bat").read_text(encoding="utf-8")
        alias = (PROJECT_ROOT / "start-observer.bat").read_text(encoding="utf-8")
        gui = (PROJECT_ROOT / "scripts/dashboard_gui.ps1").read_text(encoding="utf-8")
        launch = (PROJECT_ROOT / "scripts/launch_dashboard.ps1").read_text(encoding="utf-8")
        stop = (PROJECT_ROOT / "scripts/stop_dashboard.ps1").read_text(encoding="utf-8")
        self.assertIn("dashboard_gui.ps1", bat)
        self.assertIn("dashboard_gui.ps1", alias)
        self.assertIn("ExecutionPolicy Bypass", bat)
        self.assertIn("launch_dashboard.ps1", gui)
        self.assertIn("stop_dashboard.ps1", gui)
        self.assertIn("docker compose up -d --no-deps", launch)
        self.assertIn("8787", launch)
        self.assertIn("KEEP_DOCKER", stop)
        self.assertIn("okx-dashboard", stop)

    def test_app_and_desktop_entry_open_native_gui(self) -> None:
        command = (PROJECT_ROOT / "启动观察台.command").read_text(encoding="utf-8")
        source = (PROJECT_ROOT / "scripts/dashboard_gui.swift").read_text(encoding="utf-8")
        self.assertIn("启动观察台.app", command)
        self.assertIn("open ", command)
        self.assertIn("scripts/launch_dashboard.sh", source)
        self.assertIn("scripts/stop_dashboard.sh", source)
        self.assertIn("http://127.0.0.1:8787/healthz", source)
        self.assertNotIn("tkinter", source)
        self.assertIn("statusFromFailure", source)
        self.assertIn("runTapped()", source)
        self.assertNotIn("/Users/syndred", source)
        self.assertNotIn("请先打开 Docker Desktop", source)
        self.assertNotIn("未开 Docker 会先拉起", source)


if __name__ == "__main__":
    unittest.main()
