"""
Eigener MQTT-Broker für die Tests (docker-compose.mqtt.test.yml, Port 18830).

- gleiche mosquitto.conf und ACL wie im Betrieb,
- Passwörter werden je Testlauf zufällig erzeugt und nur gehasht abgelegt,
- der Produktiv-Broker (Port 1883) wird nicht angefasst.
"""
from __future__ import annotations

import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt

from shared.comm.mqtt_auth import MqttCredentials

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.mqtt.test.yml"
WORK_DIR = ROOT / "tests" / ".mqtt-test"
MOSQUITTO_IMAGE = "eclipse-mosquitto:2.0.18"

HOST = "localhost"
PORT = 18830


@dataclass(frozen=True)
class TestBroker:
    host: str
    port: int
    control_center: MqttCredentials
    zone: MqttCredentials


def _write_passwd(cc: MqttCredentials, zone: MqttCredentials) -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    passwd = WORK_DIR / "passwd"
    passwd.write_text(
        f"{cc.username}:{cc.password}\n{zone.username}:{zone.password}\n", encoding="utf-8"
    )
    subprocess.run(
        ["docker", "run", "--rm", "-v", f"{WORK_DIR}:/m", MOSQUITTO_IMAGE,
         "mosquitto_passwd", "-U", "/m/passwd"],
        check=True, cwd=str(ROOT),
    )


def connect_rc(host: str, port: int, creds: MqttCredentials | None, timeout_s: float = 5.0) -> int | None:
    """Verbindet einmal und liefert den CONNACK-Code (None bei Zeitüberschreitung)."""
    result: dict[str, int] = {}

    def on_connect(_c, _u, _f, rc):
        result["rc"] = rc

    client = mqtt.Client(client_id=f"probe-{secrets.token_hex(4)}")
    if creds is not None:
        client.username_pw_set(creds.username, creds.password)
    client.on_connect = on_connect
    try:
        client.connect(host, port, keepalive=10)
    except OSError:
        return None
    client.loop_start()
    deadline = time.monotonic() + timeout_s
    while "rc" not in result and time.monotonic() < deadline:
        time.sleep(0.05)
    client.loop_stop()
    client.disconnect()
    return result.get("rc")


def start() -> TestBroker:
    cc = MqttCredentials("control_center", secrets.token_urlsafe(16))
    zone = MqttCredentials("zone-01", secrets.token_urlsafe(16))
    _write_passwd(cc, zone)

    subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], check=True, cwd=str(ROOT))

    # Warten, bis der Broker Anmeldungen annimmt
    deadline = time.monotonic() + 20.0
    while connect_rc(HOST, PORT, cc, timeout_s=1.0) != 0:
        if time.monotonic() > deadline:
            stop()
            raise RuntimeError("Test-Broker nicht bereit")
        time.sleep(0.3)

    return TestBroker(host=HOST, port=PORT, control_center=cc, zone=zone)


def stop() -> None:
    subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"], check=False, cwd=str(ROOT))
    shutil.rmtree(WORK_DIR, ignore_errors=True)


def subscriber_env(broker: TestBroker, secret_hex: str, zone_id: str = "ZONE-01") -> dict[str, str]:
    """Umgebung für mqtt_subscriber.py gegen den Test-Broker."""
    import os

    env = os.environ.copy()
    env.update({
        "ZONE_ID": zone_id,
        "SENSOR_SECRET_HEX": secret_hex,
        "MQTT_BROKER_HOST": broker.host,
        "MQTT_BROKER_PORT": str(broker.port),
        "MQTT_USERNAME": broker.control_center.username,
        "MQTT_PASSWORD": broker.control_center.password,
        "DEDUP_CACHE_SIZE": "500",
        "MAX_SKEW_SECONDS": "120",
        "RATE_LIMIT_MAX_MESSAGES": "10",
        "RATE_LIMIT_WINDOW_SECONDS": "60",
        "SECURITY_POLICY_PATH": "configs/security_policy.yaml",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def zone_client(broker: TestBroker, client_id: str) -> mqtt.Client:
    """MQTT-Client mit den Zugangsdaten des Zonensensors."""
    client = mqtt.Client(client_id=client_id)
    client.username_pw_set(broker.zone.username, broker.zone.password)
    client.connect(broker.host, broker.port, keepalive=30)
    return client
