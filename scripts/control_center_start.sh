#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=lib/env.sh
source "$ROOT/scripts/lib/env.sh"
load_dotenv
require_env SENSOR_SECRET_HEX MQTT_USERNAME MQTT_PASSWORD

# Ohne Passwortdatei würde Docker an ihrer Stelle ein leeres Verzeichnis anlegen.
PASSWD_FILE="$ROOT/configs/mosquitto/passwd"
if [[ ! -f "$PASSWD_FILE" || ! -s "$PASSWD_FILE" ]]; then
  echo "[control_center_start] FEHLER: $PASSWD_FILE fehlt oder ist leer." >&2
  echo "[control_center_start] Einmalig erzeugen: ./scripts/mqtt_provision.sh <IP dieses Rechners>" >&2
  exit 1
fi

export ZONE_ID="${ZONE_ID:-ZONE-01}"

export MQTT_BROKER_HOST="${MQTT_BROKER_HOST:-localhost}"
export MQTT_BROKER_PORT="${MQTT_BROKER_PORT:-1883}"

export SECURITY_POLICY_PATH="${SECURITY_POLICY_PATH:-configs/security_policy.yaml}"
export DEDUP_CACHE_SIZE="${DEDUP_CACHE_SIZE:-500}"
export MAX_SKEW_SECONDS="${MAX_SKEW_SECONDS:-120}"

SUB_LOG="${SUB_LOG:-logs/control_center_subscriber.log}"
PID_DIR="${PID_DIR:-.run}"
mkdir -p "$(dirname "$SUB_LOG")" "$PID_DIR"

echo "[control_center_start] Project: $ROOT"
echo "[control_center_start] Starting MQTT broker (docker compose)..."
docker compose -f docker-compose.mqtt.yml up -d

echo "[control_center_start] Activating venv..."
source .venv/bin/activate


if [[ -f "$PID_DIR/subscriber.pid" ]] && kill -0 "$(cat "$PID_DIR/subscriber.pid")" 2>/dev/null; then
  echo "[control_center_start] Subscriber already running (pid=$(cat "$PID_DIR/subscriber.pid"))."
else
  echo "[control_center_start] Starting subscriber..."

  nohup python -u control_center/src/control_center/mqtt_subscriber.py >"$SUB_LOG" 2>&1 &
  echo $! > "$PID_DIR/subscriber.pid"
  echo "[control_center_start] Subscriber pid=$(cat "$PID_DIR/subscriber.pid"), log=$SUB_LOG"
fi

