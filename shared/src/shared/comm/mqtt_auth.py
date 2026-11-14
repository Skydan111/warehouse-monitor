"""
Zugangsdaten und Anmeldung am MQTT-Broker.

Benutzer und Passwort kommen ausschließlich aus den Umgebungsvariablen
MQTT_USERNAME und MQTT_PASSWORD (lokal aus .env). Es gibt keine Vorgabewerte:
Fehlt ein Wert, startet der Client nicht (Fail-Secure).

Das Passwort erscheint nie in einer Meldung.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

USERNAME_ENV = "MQTT_USERNAME"
PASSWORD_ENV = "MQTT_PASSWORD"

# Rückgabecodes von CONNACK (MQTT 3.1.1), die ein erneuter Versuch nicht behebt
RC_BAD_CREDENTIALS = 4
RC_NOT_AUTHORIZED = 5
FATAL_CONNECT_RC = {RC_BAD_CREDENTIALS, RC_NOT_AUTHORIZED}


class MqttConfigError(RuntimeError):
    """Zugangsdaten für den Broker fehlen."""


@dataclass(frozen=True)
class MqttCredentials:
    username: str
    password: str

    def __repr__(self) -> str:  # Passwort nie in Logs oder Tracebacks
        return f"MqttCredentials(username={self.username!r}, password=***)"


def load_mqtt_credentials() -> MqttCredentials:
    """Liest Benutzer und Passwort aus der Umgebung."""
    missing = [
        name for name in (USERNAME_ENV, PASSWORD_ENV)
        if not (os.environ.get(name) or "").strip()
    ]
    if missing:
        raise MqttConfigError(
            f"{', '.join(missing)} nicht gesetzt. Zugangsdaten in .env eintragen (siehe .env.example)."
        )
    return MqttCredentials(
        username=os.environ[USERNAME_ENV].strip(),
        password=os.environ[PASSWORD_ENV].strip(),
    )


def connect_rc_text(rc: int) -> str:
    """Verständliche deutsche Meldung zu einem CONNACK-Code."""
    texts = {
        0: "Verbindung angenommen",
        1: "Protokollversion abgelehnt",
        2: "Client-ID abgelehnt",
        3: "Broker nicht verfügbar",
        RC_BAD_CREDENTIALS: "Benutzer oder Passwort falsch",
        RC_NOT_AUTHORIZED: "nicht berechtigt (Anmeldung fehlt oder abgelehnt)",
    }
    return texts.get(rc, f"unbekannter Fehler (rc={rc})")
