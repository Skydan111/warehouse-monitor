"""
Anmeldung und Zugriffsliste des MQTT-Brokers (gleiche Konfiguration wie im Betrieb).

Belegt:
 - ohne Anmeldung kein Zugang, falsches Passwort wird abgelehnt,
 - Leitstand darf lesen, aber nicht senden,
 - Zonensensor darf nur in das eigene Topic senden und nichts lesen,
 - Subscriber und Publisher brechen bei falschen Zugangsdaten ab,
   statt endlos neu zu verbinden (Fail-Secure).
"""
from __future__ import annotations

import os
import secrets
import subprocess
import time
from pathlib import Path

import paho.mqtt.client as mqtt

import mqtt_test_broker
from mqtt_test_broker import connect_rc
from shared.comm.mqtt_auth import MqttCredentials
from shared.comm.topics import telemetry_topic

ROOT = Path(__file__).resolve().parents[3]
TOPIC_ZONE_01 = telemetry_topic("ZONE-01")
TOPIC_ZONE_02 = telemetry_topic("ZONE-02")


# ---------------------------------------------------------------- Anmeldung

def test_anonymous_connection_is_refused(mqtt_broker):
    rc = connect_rc(mqtt_broker.host, mqtt_broker.port, creds=None)
    assert rc is not None and rc != 0


def test_wrong_password_is_refused(mqtt_broker):
    wrong = MqttCredentials(mqtt_broker.zone.username, secrets.token_urlsafe(16))
    rc = connect_rc(mqtt_broker.host, mqtt_broker.port, wrong)
    assert rc is not None and rc != 0


def test_unknown_user_is_refused(mqtt_broker):
    rc = connect_rc(mqtt_broker.host, mqtt_broker.port, MqttCredentials("eindringling", "x"))
    assert rc is not None and rc != 0


def test_valid_credentials_are_accepted(mqtt_broker):
    assert connect_rc(mqtt_broker.host, mqtt_broker.port, mqtt_broker.control_center) == 0
    assert connect_rc(mqtt_broker.host, mqtt_broker.port, mqtt_broker.zone) == 0


# ---------------------------------------------------------------- Zugriffsliste

class Listener:
    """Abonniert ein Topic und sammelt empfangene Nachrichten."""

    def __init__(self, broker, creds: MqttCredentials, topic: str):
        self.messages: list[bytes] = []
        self.subscribed = False
        self.client = mqtt.Client(client_id=f"listener-{secrets.token_hex(4)}")
        self.client.username_pw_set(creds.username, creds.password)
        self.client.on_message = lambda _c, _u, m: self.messages.append(m.payload)
        self.client.on_subscribe = lambda *_a: setattr(self, "subscribed", True)
        self.client.connect(broker.host, broker.port, keepalive=30)
        self.client.loop_start()
        self.client.subscribe(topic, qos=1)
        deadline = time.monotonic() + 5
        while not self.subscribed and time.monotonic() < deadline:
            time.sleep(0.05)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def _publish(broker, creds: MqttCredentials, topic: str, payload: bytes) -> None:
    client = mqtt.Client(client_id=f"sender-{secrets.token_hex(4)}")
    client.username_pw_set(creds.username, creds.password)
    client.connect(broker.host, broker.port, keepalive=30)
    client.loop_start()
    client.publish(topic, payload, qos=1).wait_for_publish(timeout=3)
    client.loop_stop()
    client.disconnect()


def _received_after(listener: Listener, payload: bytes, wait_s: float = 1.0) -> bool:
    time.sleep(wait_s)
    return payload in listener.messages


def test_zone_can_send_to_own_topic_and_control_center_receives(mqtt_broker):
    listener = Listener(mqtt_broker, mqtt_broker.control_center, telemetry_topic("+"))
    try:
        payload = secrets.token_bytes(8)
        _publish(mqtt_broker, mqtt_broker.zone, TOPIC_ZONE_01, payload)
        assert _received_after(listener, payload)
    finally:
        listener.close()


def test_zone_cannot_send_to_other_zone(mqtt_broker):
    listener = Listener(mqtt_broker, mqtt_broker.control_center, telemetry_topic("+"))
    try:
        payload = secrets.token_bytes(8)
        _publish(mqtt_broker, mqtt_broker.zone, TOPIC_ZONE_02, payload)
        assert not _received_after(listener, payload)
    finally:
        listener.close()


def test_control_center_cannot_send(mqtt_broker):
    listener = Listener(mqtt_broker, mqtt_broker.control_center, telemetry_topic("+"))
    try:
        payload = secrets.token_bytes(8)
        _publish(mqtt_broker, mqtt_broker.control_center, TOPIC_ZONE_01, payload)
        assert not _received_after(listener, payload)
    finally:
        listener.close()


def test_zone_cannot_read(mqtt_broker):
    # Der Zonensensor soll fremde und eigene Messwerte nicht mitlesen können.
    listener = Listener(mqtt_broker, mqtt_broker.zone, telemetry_topic("+"))
    try:
        payload = secrets.token_bytes(8)
        _publish(mqtt_broker, mqtt_broker.zone, TOPIC_ZONE_01, payload)
        assert not _received_after(listener, payload)
    finally:
        listener.close()


# ---------------------------------------------------------------- Fail-Secure der Clients

def _run(cmd: list[str], env: dict[str, str], timeout_s: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=timeout_s)


def test_subscriber_exits_on_wrong_password(mqtt_broker):
    env = mqtt_test_broker.subscriber_env(mqtt_broker, secrets.token_hex(32))
    wrong_pw = secrets.token_urlsafe(16)
    env["MQTT_PASSWORD"] = wrong_pw

    result = _run(["python", "-u", "control_center/src/control_center/mqtt_subscriber.py"], env)

    assert result.returncode == 2
    assert "Anmeldung am Broker fehlgeschlagen" in result.stderr
    assert wrong_pw not in result.stdout + result.stderr


def test_subscriber_refuses_to_start_without_credentials(mqtt_broker):
    env = mqtt_test_broker.subscriber_env(mqtt_broker, secrets.token_hex(32))
    env.pop("MQTT_PASSWORD")

    result = _run(["python", "-u", "control_center/src/control_center/mqtt_subscriber.py"], env)

    assert result.returncode != 0
    assert "MQTT_PASSWORD nicht gesetzt" in result.stderr


def test_publisher_exits_on_wrong_password(mqtt_broker, tmp_path):
    wrong_pw = secrets.token_urlsafe(16)
    env = os.environ.copy()
    env.update({
        "ZONE_ID": "ZONE-01",
        "MQTT_BROKER_HOST": mqtt_broker.host,
        "MQTT_BROKER_PORT": str(mqtt_broker.port),
        "MQTT_USERNAME": mqtt_broker.zone.username,
        "MQTT_PASSWORD": wrong_pw,
        "TELEMETRY_FILE": str(tmp_path / "telemetry.csv"),  # existiert nicht: Sender wartet
        "PUBLISH_INTERVAL_S": "0.2",
        "PYTHONUNBUFFERED": "1",
    })

    result = _run(["python", "-u", "-m", "zone_sensor.mqtt_publisher_from_file"], env)

    assert result.returncode == 2
    assert "Anmeldung am Broker fehlgeschlagen" in result.stderr
    assert wrong_pw not in result.stdout + result.stderr
