from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "scripts/launch_dashboard.sh"
OPEN_SCRIPT = PROJECT_ROOT / "scripts/open_three_stage_dashboard.sh"


class DashboardLauncherTests(unittest.TestCase):
    def test_launcher_checks_health_before_compose_up(self) -> None:
        script = LAUNCHER.read_text(encoding="utf-8")
        self.assertLess(script.index("is_healthy"), script.index("docker compose up"))
        self.assertIn("okx-dashboard", script)
        self.assertIn("docker compose up -d --no-deps", script)
        self.assertIn('"$COMPOSE_SERVICE"', script)
        self.assertNotIn("freqtrade", script)
        self.assertIn("LAUNCHER_LOCK_DIR", script)
        self.assertIn("${TMPDIR:-/tmp}", script)
        self.assertIn("8787 已被占用，不再重复启动", script)
        self.assertIn("open -a Docker", script)
        self.assertIn("ensure_docker", script)
        self.assertIn("dashboard_path.sh", script)
        self.assertIn("powershell.exe", script)
        self.assertIn("Start-Process", script)

    def test_open_script_delegates_to_single_launcher(self) -> None:
        script = OPEN_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("launch_dashboard.sh", script)
        self.assertNotIn("docker compose up -d dashboard", script)

    def test_healthy_instance_only_opens_browser(self) -> None:
        result = self._run_launcher(healthz_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        opened = (self.fake_bin / "opened.txt").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8787", opened)
        self.assertFalse((self.fake_bin / "docker-called.txt").exists())

    def test_launcher_opens_docker_when_daemon_is_down(self) -> None:
        result = self._run_launcher(healthz_ok=False, extra_env={"DOCKER_WAIT_ATTEMPTS": "1"})
        self.assertNotEqual(result.returncode, 0)
        opened = (self.fake_bin / "opened.txt").read_text(encoding="utf-8")
        self.assertIn("Docker", opened)
        self.assertIn("正在打开 Docker Desktop", result.stderr)

    def test_second_launcher_waits_instead_of_starting_another(self) -> None:
        lock_dir = self.temp_dir / "lock"
        lock_dir.mkdir()
        (lock_dir / "pid").write_text("1", encoding="utf-8")
        result = self._run_launcher(healthz_ok=True, lock_dir=lock_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.fake_bin / "docker-called.txt").exists())

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self._tempdir.name)
        self.fake_bin = self.temp_dir / "bin"
        self.fake_bin.mkdir()
        self._write_fake_binaries()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _write_fake_binaries(self) -> None:
        curl = self.fake_bin / "curl"
        curl.write_text(
            """#!/bin/sh
if [ -f "$(dirname "$0")/healthz-ok" ]; then
  exit 0
fi
exit 22
""",
            encoding="utf-8",
        )
        docker = self.fake_bin / "docker"
        docker.write_text(
            """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/docker-called.txt"
exit 1
""",
            encoding="utf-8",
        )
        open_cmd = self.fake_bin / "open"
        open_cmd.write_text(
            """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/opened.txt"
exit 0
""",
            encoding="utf-8",
        )
        lsof = self.fake_bin / "lsof"
        lsof.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        for path in (curl, docker, open_cmd, lsof):
            path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def _run_launcher(
        self,
        *,
        healthz_ok: bool,
        lock_dir: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if healthz_ok:
            (self.fake_bin / "healthz-ok").write_text("1", encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = f"{self.fake_bin}:{env.get('PATH', '')}"
        env["LAUNCHER_LOCK_DIR"] = str(lock_dir or (self.temp_dir / "fresh-lock"))
        env["DASHBOARD_URL"] = "http://127.0.0.1:8787"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(LAUNCHER)],
            cwd=str(PROJECT_ROOT),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
