#!/usr/bin/env bash
# Rebuild the macOS control panel from scripts/dashboard_gui.swift.

set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
APP_DIR="$PROJECT_ROOT/启动观察台.app"
BIN="$APP_DIR/Contents/MacOS/launcher"

mkdir -p "$APP_DIR/Contents/MacOS"
swiftc -O -framework Cocoa -o "$BIN" "$PROJECT_ROOT/scripts/dashboard_gui.swift"
chmod +x "$BIN"
printf '%s\n' "built $BIN"
