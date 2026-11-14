"""Tests für das Laden des Geräteschlüssels (nur aus SENSOR_SECRET_HEX, Fail-Secure)."""
import secrets

import pytest

from shared.crypto.secret_env import ENV_NAME, SecretConfigError, load_sensor_secret_hex


def test_valid_key_is_returned(monkeypatch):
    key = secrets.token_hex(32)
    monkeypatch.setenv(ENV_NAME, key)
    assert load_sensor_secret_hex() == key


def test_surrounding_whitespace_is_stripped(monkeypatch):
    key = secrets.token_hex(32)
    monkeypatch.setenv(ENV_NAME, f"  {key}\n")
    assert load_sensor_secret_hex() == key


def test_missing_key_aborts(monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    with pytest.raises(SecretConfigError, match="nicht gesetzt"):
        load_sensor_secret_hex()


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_key_aborts(monkeypatch, value):
    monkeypatch.setenv(ENV_NAME, value)
    with pytest.raises(SecretConfigError, match="nicht gesetzt"):
        load_sensor_secret_hex()


@pytest.mark.parametrize(
    "value",
    [
        "deadbeef",                      # zu kurz
        secrets.token_hex(16),           # 16 Byte statt 32
        secrets.token_hex(33),           # zu lang
        "z" * 64,                        # keine Hex-Zeichen
    ],
)
def test_invalid_key_aborts(monkeypatch, value):
    monkeypatch.setenv(ENV_NAME, value)
    with pytest.raises(SecretConfigError, match="ungültig"):
        load_sensor_secret_hex()


def test_error_message_never_contains_key(monkeypatch):
    # Ein fast gültiger Schlüssel (ein Zeichen zu kurz) darf nicht im Text stehen.
    value = secrets.token_hex(32)[:-1]
    monkeypatch.setenv(ENV_NAME, value)
    with pytest.raises(SecretConfigError) as exc:
        load_sensor_secret_hex()
    assert value not in str(exc.value)


def test_legacy_variable_name_is_ignored(monkeypatch):
    # Der frühere Name HMAC_SECRET_HEX darf nicht mehr als Ersatz dienen.
    monkeypatch.delenv(ENV_NAME, raising=False)
    monkeypatch.setenv("HMAC_SECRET_HEX", secrets.token_hex(32))
    with pytest.raises(SecretConfigError):
        load_sensor_secret_hex()
