#!/usr/bin/env bash
# Stop the local dashboard and quit Docker Desktop so it does not keep using power.

set -u

CONTAINER_NAME="${DASHBOARD_CONTAINER_NAME:-okx-dashboard}"
COMPOSE_SERVICE="dashboard"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/dashboard_path.sh
. "$SCRIPT_DIR/dashboard_path.sh"
cd "$PROJECT_ROOT" || exit 1

if command -v docker >/dev/null 2>&1; then
  docker compose stop "$COMPOSE_SERVICE" >/dev/null 2>&1 || true
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

if [ "${KEEP_DOCKER:-}" = "1" ]; then
  exit 0
fi

osascript -e 'quit app "Docker"' >/dev/null 2>&1 || true
osascript -e 'quit app "Docker Desktop"' >/dev/null 2>&1 || true

if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command "Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue | Stop-Process" >/dev/null 2>&1 || true
fi
