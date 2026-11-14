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


@pytest.mark.parametrize("script", ["control_center_start.sh", "zone_sensor_start.sh"])
def test_start_script_never_prints_key(tmp_path, script):
    # Schlüssel kommt aus .env; das Skript scheitert danach an der fehlenden venv,
    # darf den Wert aber zu keinem Zeitpunkt ausgeben.
    root = _copy_scripts(tmp_path)
    key = secrets.token_hex(32)
    (root / ".env").write_text(f"{ENV_NAME}={key}\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(
        ["bash", str(root / "scripts" / script)],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=20,
    )
    assert "SENSOR_SECRET_HEX nicht gesetzt" not in result.stderr
    assert key not in result.stdout
    assert key not in result.stderr
