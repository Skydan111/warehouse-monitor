#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PID_FILE="${PID_DIR:-.run}/dashboard.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "[dashboard_stop] Stoppe Dashboard pid=$PID"
    kill "$PID" || true
  fi
  rm -f "$PID_FILE"
else
  echo "[dashboard_stop] Keine PID-Datei, Dashboard läuft nicht."
fi
