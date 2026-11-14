#!/usr/bin/env bash
# Gemeinsame Hilfsfunktionen der Startskripte.
# Wird mit "source" eingebunden, nicht direkt ausgeführt.
#
# Geheimnisse (Geräteschlüssel, später MQTT-Passwort) stehen nur in der lokalen
# Datei .env im Projektverzeichnis. Sie ist von Git ausgeschlossen.
# Vorlage: .env.example. Werte werden hier nie ausgegeben.

# Lädt $ROOT/.env, falls vorhanden. Werte aus .env haben Vorrang vor bereits
# exportierten Variablen, damit ein alter Schlüssel aus der Shell nicht
# unbemerkt weiterverwendet wird.
load_dotenv() {
  local file="$ROOT/.env"
  if [[ -f "$file" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$file"
    set +a
    echo "[env] .env geladen"
  else
    echo "[env] keine .env gefunden ($file)"
  fi
}

# Bricht ab, wenn eine der genannten Variablen fehlt oder leer ist.
# Gibt nur den Namen aus, nie den Wert.
require_env() {
  local name missing=0
  for name in "$@"; do
    if [[ -z "${!name:-}" ]]; then
      echo "[env] FEHLER: $name nicht gesetzt (in .env eintragen, siehe .env.example)" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    exit 1
  fi
}
