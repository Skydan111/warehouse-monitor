"""
Verbindungsaufbau und Wiederverbindung (Abgleichliste 9).

Belegt:
 - Leitstand-Subscriber startet VOR dem Broker, wartet und verbindet sich,
   sobald der Broker bereit ist (kein Abbruch mit ConnectionRefusedError),
 - nach einem Neustart des Brokers verbindet sich der Subscriber wieder und
   setzt das Abo neu (Nachrichten kommen wieder an),
 - Zonensensor-Publisher startet VOR dem Broker, wartet und sendet danach.
Abgelehnte Anmeldung (rc 4/5) beendet beide Clients weiterhin mit Code 2
(siehe tests/integration/security/test_mqtt_auth.py).

Hinweis: Der Subscriber schreibt wie in den übrigen MQTT-Tests nach data/.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

import mqtt_test_broker
from shared.comm.envelope import EnvelopeV1, b64_from_bytes
from shared.comm.topics import telemetry_topic
from shared.protocol.signed_csv import format_signed_line
from shared.protocol.telemetry_csv import TelemetryUnsigned

ROOT = Path(__file__).resolve().parents[3]
ZONE = "ZONE-01"


class Proc:
    """Startet einen Prozess und sammelt stdout/stderr zeilenweise in Threads."""

    def __init__(self, args: list[str], env: dict[str, str]) -> None:
        self.p = subprocess.Popen(args, cwd=str(ROOT), env=env, text=True, bufsize=1,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.out: list[str] = []
        self.err: list[str] = []
        for stream, sink in ((self.p.stdout, self.out), (self.p.stderr, self.err)):
            threading.Thread(target=self._pump, args=(stream, sink), daemon=True).start()

    @staticmethod
    def _pump(stream, sink: list[str]) -> None:
        for line in stream:
            sink.append(line)

    def text(self) -> str:
        return "STDOUT:\n" + "".join(self.out) + "\nSTDERR:\n" + "".join(self.err)

    def count(self, needle: str, stream: str = "out") -> int:
        lines = self.out if stream == "out" else self.err
        return sum(needle in l for l in list(lines))

    def wait_for(self, needle: str, stream: str = "out", times: int = 1, timeout_s: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_s
        while self.count(needle, stream) < times:
            if self.p.poll() is not None:
                time.sleep(0.2)  # restliche Ausgabe einsammeln
                raise AssertionError(f"Prozess beendet (rc={self.p.returncode}) vor '{needle}'.\n{self.text()}")
            if time.monotonic() > deadline:
                raise AssertionError(f"Zeitüberschreitung beim Warten auf '{needle}'.\n{self.text()}")
            time.sleep(0.1)

    def stop(self) -> None:
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.kill()


@pytest.fixture
def broker_paused(mqtt_broker):
    """Test-Broker angehalten; nach dem Test läuft er in jedem Fall wieder."""
    mqtt_test_broker.pause()
    try:
        yield mqtt_broker
    finally:
        mqtt_test_broker.resume(mqtt_broker)


def _signed(secret_hex: str) -> str:
    u = TelemetryUnsigned(ts=datetime.now(timezone.utc).isoformat(), temperature_c=4.2,
                          humidity_pct=50.0, pressure_hpa=1000.0, mode="sim")
    return format_signed_line(u, secret_hex)


def _publish_one(broker, secret_hex: str) -> str:
    """Sendet eine gültige Nachricht als Zonensensor und liefert ihre msg_id."""
    env = EnvelopeV1(v="1", zone_id=ZONE, msg_type="telemetry", msg_id=secrets.token_hex(8),
                     ts_utc=EnvelopeV1.now_utc_iso(), qos=1,
                     payload_b64=b64_from_bytes(_signed(secret_hex).encode("utf-8")))
    client = mqtt_test_broker.zone_client(broker, f"zone-test-{secrets.token_hex(3)}")
    client.loop_start()
    info = client.publish(telemetry_topic(ZONE), env.to_json(), qos=1)
    info.wait_for_publish(timeout=5)
    client.loop_stop()
    client.disconnect()
    return env.msg_id


def _start_subscriber(broker, secret_hex: str) -> Proc:
    env = mqtt_test_broker.subscriber_env(broker, secret_hex, ZONE)
    return Proc(["python", "-u", "control_center/src/control_center/mqtt_subscriber.py"], env)


def test_subscriber_started_before_broker_waits_and_connects(broker_paused):
    secret_hex = secrets.token_hex(32)
    sub = _start_subscriber(broker_paused, secret_hex)
    try:
        sub.wait_for("Broker nicht erreichbar", stream="err", timeout_s=10)
        time.sleep(1.5)
        assert sub.p.poll() is None, f"Subscriber hat sich beendet.\n{sub.text()}"

        mqtt_test_broker.resume(broker_paused)
        sub.wait_for("Subscribing to", timeout_s=40)

        msg_id = _publish_one(broker_paused, secret_hex)
        sub.wait_for(f"ACCEPT msg_id={msg_id}", timeout_s=10)
        assert "ConnectionRefusedError" not in sub.text()
    finally:
        sub.stop()


def test_subscriber_reconnects_and_resubscribes_after_broker_restart(mqtt_broker):
    secret_hex = secrets.token_hex(32)
    sub = _start_subscriber(mqtt_broker, secret_hex)
    try:
        sub.wait_for("Subscribing to", timeout_s=15)
        first = _publish_one(mqtt_broker, secret_hex)
        sub.wait_for(f"ACCEPT msg_id={first}", timeout_s=10)

        mqtt_test_broker.pause()
        try:
            sub.wait_for("Verbindung zum Broker verloren", stream="err", timeout_s=15)
            assert sub.p.poll() is None, f"Subscriber hat sich beendet.\n{sub.text()}"
        finally:
            mqtt_test_broker.resume(mqtt_broker)

        sub.wait_for("Subscribing to", times=2, timeout_s=40)  # Abo nach Wiederverbindung neu gesetzt
        second = _publish_one(mqtt_broker, secret_hex)
        sub.wait_for(f"ACCEPT msg_id={second}", timeout_s=10)
    finally:
        sub.stop()


def test_publisher_started_before_broker_waits_and_sends(broker_paused, tmp_path):
    secret_hex = secrets.token_hex(32)
    telemetry = tmp_path / "telemetry.csv"
    telemetry.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "ZONE_ID": ZONE,
        "MQTT_BROKER_HOST": broker_paused.host,
        "MQTT_BROKER_PORT": str(broker_paused.port),
        "MQTT_USERNAME": broker_paused.zone.username,
        "MQTT_PASSWORD": broker_paused.zone.password,
        "TELEMETRY_FILE": str(telemetry),
        "PUBLISH_INTERVAL_S": "0.2",
        "PYTHONUNBUFFERED": "1",
    })
    pub = Proc(["python", "-u", "-m", "zone_sensor.mqtt_publisher_from_file"], env)
    try:
        pub.wait_for("Broker nicht erreichbar", stream="err", timeout_s=10)
        time.sleep(1.5)
        assert pub.p.poll() is None, f"Publisher hat sich beendet.\n{pub.text()}"

        mqtt_test_broker.resume(broker_paused)
        pub.wait_for("Connected.", timeout_s=40)

        # Empfang als Leitstand prüfen
        received: list[bytes] = []
        cc = mqtt.Client(client_id=f"cc-test-{secrets.token_hex(3)}")
        cc.username_pw_set(broker_paused.control_center.username, broker_paused.control_center.password)
        cc.on_message = lambda _c, _u, m: received.append(m.payload)
        cc.connect(broker_paused.host, broker_paused.port, keepalive=10)
        cc.subscribe(telemetry_topic(ZONE), qos=1)
        cc.loop_start()
        try:
            time.sleep(0.5)
            line = _signed(secret_hex)
            with telemetry.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            deadline = time.monotonic() + 10
            while not received and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            cc.loop_stop()
            cc.disconnect()

        assert received, f"Nichts empfangen.\n{pub.text()}"
        assert EnvelopeV1.from_json(received[0].decode()).payload_b64 == b64_from_bytes(line.encode("utf-8"))
    finally:
        pub.stop()
