"""
Verbindungsaufbau und Wiederverbindung für beide MQTT-Clients
(Leitstand-Subscriber und Zonensensor-Publisher).

- Wartezeit zwischen Versuchen: 1 s, verdoppelt bis höchstens 30 s
  (paho-Standard wäre bis 120 s).
- Auch der ERSTE Verbindungsaufbau wird wiederholt (connect_async):
  Ist der Broker beim Start noch nicht bereit, beendet sich der Client
  nicht mit ConnectionRefusedError, sondern wartet.
- Abgelehnte Anmeldung (CONNACK 4/5) wird NICHT wiederholt; das behandeln
  die on_connect-Callbacks der Clients (Ende mit Code 2).

Die Meldungen enthalten nie Zugangsdaten.
"""
from __future__ import annotations

import sys

import paho.mqtt.client as mqtt

RECONNECT_MIN_DELAY_S = 1
RECONNECT_MAX_DELAY_S = 30


def configure_reconnect(client: mqtt.Client, log_prefix: str) -> None:
    """Setzt Wartezeiten und Protokoll-Callbacks für Verbindungsabbrüche."""
    client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S, max_delay=RECONNECT_MAX_DELAY_S)

    def on_disconnect(_client: mqtt.Client, _userdata: object, rc: int) -> None:
        # rc == 0: gewolltes disconnect() (z. B. Programmende oder abgelehnte Anmeldung)
        if rc != 0:
            print(
                f"{log_prefix} Verbindung zum Broker verloren (rc={rc}). "
                f"Neuer Versuch in {RECONNECT_MIN_DELAY_S}–{RECONNECT_MAX_DELAY_S} s.",
                file=sys.stderr,
            )

    def on_connect_fail(_client: mqtt.Client, _userdata: object) -> None:
        # Broker nicht erreichbar (TCP), z. B. noch nicht gestartet oder Netz weg
        print(
            f"{log_prefix} Broker nicht erreichbar. "
            f"Neuer Versuch in {RECONNECT_MIN_DELAY_S}–{RECONNECT_MAX_DELAY_S} s.",
            file=sys.stderr,
        )

    client.on_disconnect = on_disconnect
    client.on_connect_fail = on_connect_fail
