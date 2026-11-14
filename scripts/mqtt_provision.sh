#!/usr/bin/env bash
# Erzeugt neue Geheimnisse für Leitstand und Zonensensor ZONE-01.
# Läuft einmalig auf dem Rechner des Leitstands (dort läuft der Broker).
#
#   ./scripts/mqtt_provision.sh <IP des Leitstands> [--force]
#
# Ergebnis (alle Dateien chmod 600, von Git ausgeschlossen):
#   .env                      Leitstand: Geräteschlüssel, MQTT-Benutzer control_center
#   .env.zone-01              Zonensensor: gleicher Geräteschlüssel, MQTT-Benutzer zone-01,
#                             Adresse des Brokers -> auf den Zonensensor als .env kopieren
#   configs/mosquitto/passwd  gehashte Passwörter für den Broker
#
# Kein Wert wird auf dem Bildschirm ausgegeben.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BROKER_HOST="${1:-}"
FORCE="${2:-}"
MOSQUITTO_IMAGE="eclipse-mosquitto:2.0.18"
PASSWD_FILE="configs/mosquitto/passwd"

if [[ -z "$BROKER_HOST" || "$BROKER_HOST" == --* ]]; then
  echo "Aufruf: $0 <IP des Leitstands> [--force]" >&2
  exit 1
fi

if [[ "$FORCE" != "--force" ]]; then
  for f in .env .env.zone-01 "$PASSWD_FILE"; do
    if [[ -e "$f" ]]; then
      echo "[provision] FEHLER: $f existiert bereits. Mit --force alles neu erzeugen (alte Werte werden ungültig)." >&2
      exit 1
    fi
  done
fi

umask 077

# Bei einem Fehler darf keine Passwortdatei im Klartext liegen bleiben.
cleanup_on_error() {
  rm -f "$PASSWD_FILE"
  echo "[provision] FEHLER: abgebrochen, $PASSWD_FILE entfernt." >&2
}
trap cleanup_on_error ERR

echo "[provision] Erzeuge Geräteschlüssel und MQTT-Passwörter ..."
python3 - "$BROKER_HOST" "$PASSWD_FILE" <<'PY'
import os, secrets, sys
broker_host, passwd_file = sys.argv[1], sys.argv[2]

key = secrets.token_hex(32)              # 32 Byte HMAC-Schlüssel
pw_cc = secrets.token_urlsafe(24)        # Passwort Leitstand
pw_zone = secrets.token_urlsafe(24)      # Passwort Zonensensor

def write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)

write(".env",
      "# Leitstand – erzeugt von scripts/mqtt_provision.sh\n"
      f"SENSOR_SECRET_HEX={key}\n"
      "MQTT_USERNAME=control_center\n"
      f"MQTT_PASSWORD={pw_cc}\n")
write(".env.zone-01",
      "# Zonensensor ZONE-01 – auf dem Raspberry Pi als .env ablegen\n"
      f"SENSOR_SECRET_HEX={key}\n"
      "MQTT_USERNAME=zone-01\n"
      f"MQTT_PASSWORD={pw_zone}\n"
      f"MQTT_BROKER_HOST={broker_host}\n")
# Klartext, wird direkt danach von mosquitto_passwd gehasht
write(passwd_file, f"control_center:{pw_cc}\nzone-01:{pw_zone}\n")
PY

echo "[provision] Hashe Passwortdatei mit $MOSQUITTO_IMAGE ..."
docker run --rm -v "$ROOT/configs/mosquitto:/m" "$MOSQUITTO_IMAGE" mosquitto_passwd -U /m/passwd

# Kontrolle: jede Zeile muss einen Hash enthalten, kein Klartext.
if ! awk -F: 'NF!=2 || $2 !~ /^\$[67]\$/ {bad=1} END {exit bad}' "$PASSWD_FILE"; then
  false  # löst cleanup_on_error aus
fi
chmod 600 "$PASSWD_FILE"
trap - ERR

echo "[provision] Fertig:"
echo "  .env                     (Leitstand)"
echo "  .env.zone-01             (für den Zonensensor, Broker: $BROKER_HOST)"
echo "  $PASSWD_FILE  (gehasht)"
echo "[provision] Broker neu starten, damit er die Passwortdatei liest:"
echo "  ./scripts/control_center_stop.sh && ./scripts/control_center_start.sh"
