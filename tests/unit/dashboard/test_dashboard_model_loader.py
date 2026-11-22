"""Prüfsummenkontrolle des KI-Modells (Abgleichliste 11).

Kleine Ersatzdatei statt der echten 1,9-GB-Modelldatei; geladen wird mit
einem Ersatz-Loader, llama-cpp-python wird nicht benötigt.
"""
from __future__ import annotations

import hashlib

import pytest

from dashboard.backend.services.model_loader import (
    ModelUnavailable,
    prepare_model,
    read_expected_sha256,
    sha256_file,
)


@pytest.fixture(autouse=True)
def no_env_override(monkeypatch):
    monkeypatch.delenv("MODEL_SHA256", raising=False)


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "warehouse_model.gguf"
    path.write_bytes(b"GGUF-Testinhalt" * 1000)
    sha = tmp_path / "warehouse_model.gguf.sha256"
    sha.write_text(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  warehouse_model.gguf\n")
    return path, sha


def test_sha256_file_matches_hashlib(model):
    path, _ = model
    assert sha256_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_correct_checksum_loads_model(model):
    path, sha = model
    loaded = prepare_model(path, sha, loader=lambda p: ("geladen", p))
    assert loaded == ("geladen", str(path))


def test_modified_model_is_rejected_and_not_loaded(model):
    path, sha = model
    path.write_bytes(path.read_bytes() + b"x")  # ein Byte angehängt
    calls = []
    with pytest.raises(ModelUnavailable, match="Prüfsumme"):
        prepare_model(path, sha, loader=calls.append)
    assert calls == []  # fail-secure: Laden wird gar nicht erst versucht


def test_missing_checksum_file_blocks_loading(model):
    path, sha = model
    sha.unlink()
    with pytest.raises(ModelUnavailable, match="Prüfsummendatei fehlt"):
        prepare_model(path, sha, loader=lambda p: p)


@pytest.mark.parametrize("content", ["", "abc", "z" * 64, "12" * 33])
def test_invalid_expected_checksum_is_rejected(model, content):
    _, sha = model
    sha.write_text(content)
    with pytest.raises(ModelUnavailable, match="ungültig"):
        read_expected_sha256(sha)


def test_env_override_has_priority(model, monkeypatch):
    path, sha = model
    monkeypatch.setenv("MODEL_SHA256", "ab" * 32)
    assert read_expected_sha256(sha) == "ab" * 32
    with pytest.raises(ModelUnavailable, match="Prüfsumme"):
        prepare_model(path, sha, loader=lambda p: p)


def test_uppercase_checksum_is_accepted(model):
    path, sha = model
    sha.write_text(sha.read_text().upper())
    assert prepare_model(path, sha, loader=lambda p: "ok") == "ok"


def test_missing_model_file(model):
    path, sha = model
    path.unlink()
    with pytest.raises(ModelUnavailable, match="nicht gefunden"):
        prepare_model(path, sha, loader=lambda p: p)


def test_loader_error_becomes_model_unavailable(model):
    path, sha = model

    def broken(_):
        raise ValueError("kaputte GGUF")

    with pytest.raises(ModelUnavailable, match="konnte nicht geladen"):
        prepare_model(path, sha, loader=broken)


def test_versioned_checksum_file_is_valid():
    from dashboard.backend.config import MODEL_SHA256_PATH

    assert len(read_expected_sha256(MODEL_SHA256_PATH)) == 64
