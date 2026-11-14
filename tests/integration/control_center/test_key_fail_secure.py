"""
Fail-Secure beim Geräteschlüssel im Leitstand:
 - kein Rückfall auf eine Konfigurationsdatei,
 - fehlender Schlüssel führt zum Abbruch statt zur Ablage als "malformed",
 - Startskripte brechen ohne Schlüssel ab, bevor etwas gestartet wird.
"""
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

from control_center import packet_pipeline, verify
from shared.crypto.hmac_sha256 import sign as hmac_sign
from shared.crypto.secret_env import ENV_NAME, SecretConfigError

ROOT = Path(__file__).resolve().parents[3]
PAYLOAD = "2025-11-24T17:52:54Z,22.50,45.20,1013.20,NOMINAL"


def test_config_file_with_key_no_longer_exists():
    assert not (ROOT / "configs" / "control_center.json").exists()


def test_verify_without_key_aborts(monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    monkeypatch.delenv("HMAC_SECRET_HEX", raising=False)
    with pytest.raises(SecretConfigError):
        verify.verify_with_env_key(PAYLOAD.encode(), "00" * 32)


def test_verify_uses_env_key(monkeypatch):
    key = secrets.token_hex(32)
    monkeypatch.setenv(ENV_NAME, key)
    assert verify.verify_with_env_key(PAYLOAD.encode(), hmac_sign(PAYLOAD, key)) is True
    assert verify.verify_with_env_key(PAYLOAD.encode(), hmac_sign(PAYLOAD, secrets.token_hex(32))) is False


def test_pipeline_without_key_raises_and_writes_nothing(monkeypatch, tmp_path):
    proc = tmp_path / "processed.csv"
    rej = tmp_path / "rejected.csv"
    monkeypatch.setattr(packet_pipeline, "PROC_PATH", proc)
    monkeypatch.setattr(packet_pipeline, "REJ_PATH", rej)
    monkeypatch.delenv(ENV_NAME, raising=False)

    line = f"{PAYLOAD},{'00' * 32}"
    with pytest.raises(SecretConfigError):
        packet_pipeline.handle_line(line, secman=None, source="test")

    assert not proc.exists()
    assert not rej.exists()


def _copy_scripts(tmp_path: Path) -> Path:
    # Skripte in ein leeres Projektverzeichnis ohne .env kopieren.
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    return tmp_path


@pytest.mark.parametrize("script", ["control_center_start.sh", "zone_sensor_start.sh"])
def test_start_script_aborts_without_key(tmp_path, script):
    root = _copy_scripts(tmp_path)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    result = subprocess.run(
        ["bash", str(root / "scripts" / script)],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 1
    assert "SENSOR_SECRET_HEX nicht gesetzt" in result.stderr
    # Abbruch vor Docker bzw. vor dem Start der Python-Prozesse
    assert "Starting" not in result.stdout
    assert not (root / ".run").exists()


def _write_env(root: Path, **values: str) -> None:
    lines = [f"{k}={v}" for k, v in values.items()]
    (root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_script(root: Path, script: str) -> subprocess.CompletedProcess[str]:
    # Minimaler PATH: kein docker, keine venv -> Skript kann nichts Echtes starten.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(root)}
    return subprocess.run(
        ["bash", str(root / "scripts" / script)],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=20,
    )


@pytest.mark.parametrize("script", ["control_center_start.sh", "zone_sensor_start.sh"])
def test_start_script_never_prints_secrets(tmp_path, script):
    root = _copy_scripts(tmp_path)
    key = secrets.token_hex(32)
    password = secrets.token_urlsafe(24)
    _write_env(root, SENSOR_SECRET_HEX=key, MQTT_USERNAME="zone-01",
               MQTT_PASSWORD=password, MQTT_BROKER_HOST="192.0.2.1")

    result = _run_script(root, script)

    assert "nicht gesetzt" not in result.stderr
    for secret in (key, password):
        assert secret not in result.stdout
        assert secret not in result.stderr


@pytest.mark.parametrize(
    "script, missing",
    [
        ("control_center_start.sh", "MQTT_USERNAME"),
        ("control_center_start.sh", "MQTT_PASSWORD"),
        ("zone_sensor_start.sh", "MQTT_USERNAME"),
        ("zone_sensor_start.sh", "MQTT_PASSWORD"),
        ("zone_sensor_start.sh", "MQTT_BROKER_HOST"),
    ],
)
def test_start_script_aborts_without_mqtt_settings(tmp_path, script, missing):
    root = _copy_scripts(tmp_path)
    values = {"SENSOR_SECRET_HEX": secrets.token_hex(32), "MQTT_USERNAME": "u",
              "MQTT_PASSWORD": "p", "MQTT_BROKER_HOST": "192.0.2.1"}
    values.pop(missing)
    _write_env(root, **values)

    result = _run_script(root, script)

    assert result.returncode == 1
    assert f"{missing} nicht gesetzt" in result.stderr
    assert not (root / ".run").exists()


@pytest.mark.parametrize("make_dir", [False, True])
def test_control_center_start_aborts_without_passwd_file(tmp_path, make_dir):
    # make_dir=True: Docker hat beim früheren Start ohne Datei ein Verzeichnis angelegt.
    root = _copy_scripts(tmp_path)
    if make_dir:
        (root / "configs" / "mosquitto" / "passwd").mkdir(parents=True)
    _write_env(root, SENSOR_SECRET_HEX=secrets.token_hex(32),
               MQTT_USERNAME="control_center", MQTT_PASSWORD="p")

    result = _run_script(root, "control_center_start.sh")

    assert result.returncode == 1
    assert "passwd fehlt oder ist leer" in result.stderr
    assert "Starting MQTT broker" not in result.stdout
