"""
Laden des Geräteschlüssels aus der Umgebung.

Der Schlüssel wird ausschließlich aus der Umgebungsvariable SENSOR_SECRET_HEX
gelesen. Es gibt keinen Ersatzschlüssel und keinen Rückfall auf eine Datei:
Fehlt der Schlüssel oder ist er ungültig, bricht der Aufrufer ab (Fail-Secure).

Der Wert des Schlüssels erscheint nie in einer Fehlermeldung.
"""
from __future__ import annotations

import os
import re

ENV_NAME = "SENSOR_SECRET_HEX"
KEY_HEX_LENGTH = 64  # 32 Byte

_HEX_RE = re.compile(r"[0-9a-fA-F]+")


class SecretConfigError(RuntimeError):
    """Geräteschlüssel fehlt oder ist ungültig."""


def load_sensor_secret_hex() -> str:
    """Liest und prüft den Geräteschlüssel aus SENSOR_SECRET_HEX."""
    value = os.environ.get(ENV_NAME)
    if value is None or not value.strip():
        raise SecretConfigError(
            f"{ENV_NAME} nicht gesetzt. Schlüssel in .env eintragen (siehe .env.example)."
        )

    key = value.strip()
    if len(key) != KEY_HEX_LENGTH or not _HEX_RE.fullmatch(key):
        raise SecretConfigError(
            f"{ENV_NAME} ungültig: erwartet werden genau {KEY_HEX_LENGTH} Hex-Zeichen "
            f"(32 Byte), erhalten {len(key)} Zeichen."
        )
    return key
