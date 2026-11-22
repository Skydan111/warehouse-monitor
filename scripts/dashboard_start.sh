#!/usr/bin/env bash
# Startet das Dashboard (FastAPI/uvicorn) im Hintergrund.
#
# Sicherheit: Der Server bindet fest an 127.0.0.1. Das Dashboard ist damit nur
# auf diesem Rechner erreichbar, nicht aus dem Netz (Abgleich 12).
# Die .env wird bewusst nicht geladen: Das Dashboard braucht weder den
# Geräteschlüssel noch das MQTT-Passwort (geringste Rechte).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="127.0.0.1"
PORT="${DASHBOARD_PORT:-8000}"
LOG="${DASHBOARD_LOG:-logs/dashboard.log}"
PID_DIR="${PID_DIR:-.run}"
PID_FILE="$PID_DIR/dashboard.pid"
mkdir -p "$(dirname "$LOG")" "$PID_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "[dashboard_start] Dashboard läuft bereits (pid=$(cat "$PID_FILE")): http://$HOST:$PORT"
  exit 0
fi

source .venv/bin/activate

echo "[dashboard_start] Starte Dashboard auf http://$HOST:$PORT ..."
nohup python -u -m uvicorn dashboard.backend.main:app --host "$HOST" --port "$PORT" >"$LOG" 2>&1 &
echo $! > "$PID_FILE"

# Beim Start wird die Prüfsumme des Modells berechnet und das Modell geladen.
for _ in $(seq 1 120); do
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "[dashboard_start] FEHLER: Dashboard beendet, siehe $LOG" >&2
    rm -f "$PID_FILE"
    exit 1
  fi
  if curl -fsS "http://$HOST:$PORT/telemetry/thresholds" >/dev/null 2>&1; then
    echo "[dashboard_start] Bereit: http://$HOST:$PORT (pid=$(cat "$PID_FILE"), log=$LOG)"
    if grep -q "KI-Analyse deaktiviert" "$LOG"; then
      echo "[dashboard_start] WARNUNG: $(grep "KI-Analyse deaktiviert" "$LOG" | tail -n 1)" >&2
    fi
    exit 0
  fi
  sleep 1
done

echo "[dashboard_start] FEHLER: Dashboard antwortet nicht nach 120 s, siehe $LOG" >&2
exit 1
