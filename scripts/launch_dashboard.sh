#!/usr/bin/env bash
# Start the local dashboard once. If it is already healthy, only open the page.

set -u

DASHBOARD_URL="${DASHBOARD_URL:-http://127.0.0.1:8787}"
HEALTHZ_URL="${DASHBOARD_URL%/}/healthz"
CONTAINER_NAME="${DASHBOARD_CONTAINER_NAME:-okx-dashboard}"
COMPOSE_SERVICE="dashboard"
LOCK_DIR="${LAUNCHER_LOCK_DIR:-${TMPDIR:-/tmp}/okx-dashboard-launcher.lock}"
LOCK_STALE_SECONDS=90
WAIT_ATTEMPTS=30
DOCKER_WAIT_ATTEMPTS="${DOCKER_WAIT_ATTEMPTS:-90}"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/dashboard_path.sh
. "$SCRIPT_DIR/dashboard_path.sh"
cd "$PROJECT_ROOT" || exit 1

is_healthy() {
  curl --fail --silent --show-error --max-time 2 "$HEALTHZ_URL" >/dev/null 2>&1
}

open_page() {
  if command -v open >/dev/null 2>&1; then
    open "$DASHBOARD_URL"
    return $?
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$DASHBOARD_URL" >/dev/null 2>&1
    return $?
  fi
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "Start-Process '$DASHBOARD_URL'" >/dev/null 2>&1
    return $?
  fi
  if command -v cmd.exe >/dev/null 2>&1; then
    cmd.exe /c start "" "$DASHBOARD_URL" >/dev/null 2>&1
    return $?
  fi
  printf '%s\n' "$DASHBOARD_URL"
  return 0
}

lock_age_seconds() {
  if [ ! -d "$LOCK_DIR" ]; then
    printf '%s\n' "0"
    return 0
  fi
  python3 - "$LOCK_DIR" <<'PY'
import os
import sys
import time

path = sys.argv[1]
try:
    print(int(time.time() - os.stat(path).st_mtime))
except OSError:
    print(0)
PY
}

release_lock() {
  rm -rf "$LOCK_DIR" 2>/dev/null || true
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
    return 0
  fi

  age="$(lock_age_seconds)"
  if [ "${age:-0}" -ge "$LOCK_STALE_SECONDS" ]; then
    release_lock
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      printf '%s\n' "$$" >"$LOCK_DIR/pid"
      return 0
    fi
  fi
  return 1
}

docker_ready() {
  docker info >/dev/null 2>&1
}

open_docker_desktop() {
  if [ -d "/Applications/Docker.app" ]; then
    open -a Docker >/dev/null 2>&1 || true
    return 0
  fi
  if command -v open >/dev/null 2>&1; then
    open -a "Docker Desktop" >/dev/null 2>&1 || open -a Docker >/dev/null 2>&1 || true
    return 0
  fi
  if [ -x "/c/Program Files/Docker/Docker/Docker Desktop.exe" ]; then
    "/c/Program Files/Docker/Docker/Docker Desktop.exe" >/dev/null 2>&1 &
    return 0
  fi
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "Start-Process 'Docker Desktop'" >/dev/null 2>&1 || true
  fi
}

ensure_docker() {
  if docker_ready; then
    return 0
  fi
  printf '%s\n' "正在打开 Docker Desktop…" >&2
  open_docker_desktop
  local attempt
  for attempt in $(seq 1 "$DOCKER_WAIT_ATTEMPTS"); do
    if docker_ready; then
      return 0
    fi
    sleep 1
  done
  printf '%s\n' "Docker 还没就绪，请确认 Docker Desktop 已安装并完成启动。" >&2
  return 1
}

container_state() {
  docker inspect -f '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || true
}

port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  python3 - <<'PY'
import socket
import sys

sock = socket.socket()
sock.settimeout(0.4)
try:
    sock.connect(("127.0.0.1", 8787))
except OSError:
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PY
}

wait_until_healthy() {
  local attempt
  for attempt in $(seq 1 "$WAIT_ATTEMPTS"); do
    if is_healthy; then
      return 0
    fi
    if [ "$attempt" -lt "$WAIT_ATTEMPTS" ]; then
      sleep 1
    fi
  done
  return 1
}

show_logs() {
  docker compose logs --tail 80 "$COMPOSE_SERVICE" 2>/dev/null || true
  docker logs --tail 80 "$CONTAINER_NAME" 2>/dev/null || true
}

if is_healthy; then
  open_page
  exit $?
fi

if ! acquire_lock; then
  if wait_until_healthy; then
    open_page
    exit $?
  fi
  printf '%s\n' "观察台正在启动，请稍后再点一次。" >&2
  exit 1
fi
trap release_lock EXIT

if is_healthy; then
  open_page
  exit $?
fi

if ! ensure_docker; then
  exit 1
fi

state="$(container_state)"
if [ "$state" = "running" ]; then
  if wait_until_healthy; then
    open_page
    exit $?
  fi
  printf '%s\n' "观察台容器已在运行，但页面还没就绪。" >&2
  show_logs
  exit 1
fi

if [ "$state" = "created" ] || [ "$state" = "exited" ] || [ "$state" = "paused" ]; then
  if [ "$state" = "paused" ]; then
    docker unpause "$CONTAINER_NAME" >/dev/null
  fi
  if ! docker start "$CONTAINER_NAME" >/dev/null; then
    printf '%s\n' "无法恢复已有观察台容器。" >&2
    show_logs
    exit 1
  fi
  if wait_until_healthy; then
    open_page
    exit $?
  fi
  printf '%s\n' "已有观察台容器已启动，但页面还没就绪。" >&2
  show_logs
  exit 1
fi

if port_in_use; then
  if wait_until_healthy; then
    open_page
    exit $?
  fi
  printf '%s\n' "8787 已被占用，不再重复启动。打开已有页面失败。" >&2
  exit 1
fi

if ! docker compose up -d --no-deps "$COMPOSE_SERVICE"; then
  show_logs
  exit 1
fi

if wait_until_healthy; then
  open_page
  exit $?
fi

show_logs
exit 1
