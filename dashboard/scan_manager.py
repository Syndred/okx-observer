"""Single-task execution and progress tracking for the local dashboard scan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from dashboard.redaction import redact_sensitive_text


ProgressCallback = Callable[[str], None]
ScanRunner = Callable[[ProgressCallback], object]

MAX_LOG_LINES = 40
MAX_ERROR_LINES = 5
MAX_LOG_LINE_LENGTH = 500
PROCESS_TERMINATE_TIMEOUT = 2
PRODUCTION_REPORT_DIR = Path("/freqtrade/reports/okx-three-stage-screener")
REPORT_ARTIFACTS = ("LATEST.md", "latest.csv", "run-manifest.json")
_REPORT_ARTIFACT_LOCK = threading.RLock()

_ANSI_ESCAPE = re.compile(
    r"\x1b(?:[\[\]()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-Za-z]?|[@-Z\\-_])"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STAGE_MARKERS = (
    ("[1/3]", "daily"),
    ("[2/3]", "four_hour"),
    ("[3/3]", "fifteen_minute"),
)


@contextmanager
def report_artifact_lock():
    """Serialize API reads with the short publication transaction."""

    with _REPORT_ARTIFACT_LOCK:
        yield


@dataclass(frozen=True)
class ScanSnapshot:
    """Public immutable representation of one scan's current state."""

    state: str
    stage: str | None
    started_at: str | None
    finished_at: str | None
    error: str | None
    log_tail: list[str]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly copy without exposing mutable internals."""

        return {
            "state": self.state,
            "stage": self.stage,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "log_tail": list(self.log_tail),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_log_line(value: object) -> str:
    """Strip terminal/control characters and redact obvious secret values."""

    text = str(value).replace("\r", "").replace("\n", "")
    text = _ANSI_ESCAPE.sub("", text)
    text = _CONTROL_CHARS.sub("", text).strip()
    return str(redact_sensitive_text(text))[:MAX_LOG_LINE_LENGTH]


def _error_from_exception(error: BaseException, log_tail: list[str]) -> str:
    """Build a bounded failure message from the final logs or exception text."""

    recent = [line for line in log_tail[-MAX_ERROR_LINES:] if line]
    if recent:
        return "\n".join(recent)
    cleaned = clean_log_line(str(error))
    return cleaned or type(error).__name__


def _backup_report_artifacts(
    report_dir: Path,
) -> tuple[Path, set[str]]:
    """Copy only the three published artifacts to a sibling temporary folder."""

    backup_dir = Path(
        tempfile.mkdtemp(prefix=".dashboard-scan-backup-", dir=report_dir.parent)
    )
    present: set[str] = set()
    try:
        for name in REPORT_ARTIFACTS:
            source = report_dir / name
            if source.is_file():
                shutil.copy2(source, backup_dir / name)
                present.add(name)
    except BaseException:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise
    return backup_dir, present


def _restore_report_artifacts(
    report_dir: Path,
    backup_dir: Path,
    present: set[str],
) -> None:
    """Restore bytes and existence of published artifacts after a failed scan."""

    report_dir.mkdir(parents=True, exist_ok=True)
    for name in REPORT_ARTIFACTS:
        destination = report_dir / name
        if name in present:
            shutil.copy2(backup_dir / name, destination)
        elif destination.exists() and destination.is_file():
            destination.unlink()


def _cleanup_report_backup(backup_dir: Path | None) -> None:
    if backup_dir is not None:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _publish_report_artifacts(report_dir: Path, staging_dir: Path) -> None:
    """Publish a complete staged report while readers hold the same lock."""

    missing = [
        name
        for name in REPORT_ARTIFACTS
        if not (staging_dir / name).is_file()
    ]
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"scan did not produce report artifacts: {names}")

    with report_artifact_lock():
        backup_dir, present = _backup_report_artifacts(report_dir)
        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            for name in REPORT_ARTIFACTS:
                (staging_dir / name).replace(report_dir / name)
        except BaseException:
            _restore_report_artifacts(report_dir, backup_dir, present)
            raise
        finally:
            _cleanup_report_backup(backup_dir)


def _reap_process(process: object) -> None:
    """Terminate a still-running child, then always reap it."""

    try:
        running = process.poll() is None  # type: ignore[attr-defined]
    except BaseException:
        running = True
    if running:
        try:
            process.terminate()  # type: ignore[attr-defined]
        except BaseException:
            pass
        try:
            process.wait(timeout=PROCESS_TERMINATE_TIMEOUT)  # type: ignore[attr-defined]
        except subprocess.TimeoutExpired:
            try:
                process.kill()  # type: ignore[attr-defined]
            except BaseException:
                pass
            try:
                process.wait(timeout=PROCESS_TERMINATE_TIMEOUT)  # type: ignore[attr-defined]
            except BaseException:
                pass
        except BaseException:
            pass
    else:
        try:
            process.wait()  # type: ignore[attr-defined]
        except BaseException:
            pass


def run_production_scan(progress: ProgressCallback) -> None:
    """Run the one supported scan command and stream its output to ``progress``."""

    report_dir = PRODUCTION_REPORT_DIR
    report_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=".dashboard-scan-output-",
            dir=report_dir.parent,
        )
    )
    command = [
        sys.executable,
        "/freqtrade/scripts/scan_okx_three_stage.py",
        "--output-dir",
        str(staging_dir),
        "--workers",
        "8",
        "--requests-per-second",
        "12",
    ]
    process = None
    return_code: int | None = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd="/freqtrade",
        )
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    progress(line)
            return_code = process.wait()
        finally:
            try:
                if process.stdout is not None:
                    process.stdout.close()
            finally:
                _reap_process(process)
        if return_code != 0:
            raise RuntimeError(f"scan command exited with status {return_code}")
        _publish_report_artifacts(report_dir, staging_dir)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


class ScanManager:
    """Run at most one scan at a time and retain a bounded status snapshot."""

    def __init__(
        self,
        runner: ScanRunner | None = None,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self._runner = runner or run_production_scan
        self._thread_factory = thread_factory
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active = False
        self._state = "idle"
        self._stage: str | None = None
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._error: str | None = None
        self._log_tail: list[str] = []

    def start(self) -> bool:
        """Start a daemon worker unless another scan is currently active."""

        with self._lock:
            if self._active:
                return False
            self._active = True
            self._state = "running"
            self._stage = None
            self._started_at = _utc_now()
            self._finished_at = None
            self._error = None
            self._log_tail = []
            try:
                self._thread = self._thread_factory(
                    target=self._run,
                    name="three-stage-scan",
                    daemon=True,
                )
                self._thread.start()
            except BaseException as error:
                self._thread = None
                self._active = False
                self._state = "failed"
                self._error = _error_from_exception(error, [])
                self._finished_at = _utc_now()
                return False
            return True

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the current worker, if any, to finish."""

        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def snapshot(self) -> dict[str, object]:
        """Return a thread-safe, JSON-friendly status dictionary."""

        with self._lock:
            snapshot = ScanSnapshot(
                state=self._state,
                stage=self._stage,
                started_at=self._started_at,
                finished_at=self._finished_at,
                error=self._error,
                log_tail=list(self._log_tail),
            )
        return snapshot.as_dict()

    def _record_progress(self, value: object) -> None:
        line = clean_log_line(value)
        if not line:
            return
        with self._lock:
            self._log_tail.append(line)
            if len(self._log_tail) > MAX_LOG_LINES:
                del self._log_tail[:-MAX_LOG_LINES]
            for marker, stage in _STAGE_MARKERS:
                if marker in line:
                    self._stage = stage

    def _run(self) -> None:
        try:
            result = self._runner(self._record_progress)
            if (
                isinstance(result, int)
                and not isinstance(result, bool)
                and result != 0
            ):
                raise RuntimeError(f"scan command exited with status {result}")
        except BaseException as error:
            with self._lock:
                self._state = "failed"
                self._error = _error_from_exception(error, self._log_tail)
                self._finished_at = _utc_now()
                self._active = False
        else:
            with self._lock:
                self._state = "succeeded"
                self._error = None
                self._finished_at = _utc_now()
                self._active = False
