"""Laden des KI-Modells mit Prüfsummenkontrolle (fail-secure).

Ablauf: Datei vorhanden → erwartete SHA-256 bekannt → SHA-256 der Datei stimmt
→ erst dann wird das Modell geladen. Jeder Fehler endet in ModelUnavailable;
das Dashboard läuft weiter, nur /analyse antwortet mit 503.

Schutzumfang: erkennt eine beschädigte, unvollständig kopierte oder vertauschte
Modelldatei. Gegen einen Angreifer mit Schreibzugriff auf den Rechner schützt
die Prüfsumme nicht, er könnte Modell und Prüfsummendatei gemeinsam ersetzen.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # llama_cpp nur zur Laufzeit in load_model importieren
    from llama_cpp import Llama

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1024 * 1024


class ModelUnavailable(Exception):
    """Modell darf nicht verwendet werden; Text ist für den Bediener bestimmt."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_expected_sha256(sha_path: Path) -> str:
    """Erwartete Prüfsumme: MODEL_SHA256 aus der Umgebung, sonst die versionierte Datei.

    Dateiformat wie die Ausgabe von `shasum -a 256` („<hex>  <dateiname>“).
    """
    raw = os.environ.get("MODEL_SHA256", "").strip()
    if not raw:
        if not sha_path.exists():
            raise ModelUnavailable(f"Prüfsummendatei fehlt: {sha_path.name}")
        content = sha_path.read_text(encoding="utf-8").split()
        raw = content[0] if content else ""
    value = raw.lower()
    if not _HEX64.match(value):
        raise ModelUnavailable("Erwartete Prüfsumme des KI-Modells ist ungültig.")
    return value


def load_model(model_path: str) -> "Llama":
    from llama_cpp import Llama

    return Llama(
        model_path=model_path,
        n_ctx=2048,
        n_threads=4,
        n_gpu_layers=-1,   # alle Layer auf die GPU (Metal); ohne GPU-Build automatisch CPU
        verbose=False,
    )


def prepare_model(
    model_path: Path,
    sha_path: Path,
    loader: Callable[[str], object] = load_model,
):
    """Prüft die Modelldatei und lädt sie erst danach."""
    if not model_path.exists():
        raise ModelUnavailable(f"KI-Modell nicht gefunden: {model_path.name}")

    expected = read_expected_sha256(sha_path)
    actual = sha256_file(model_path)
    if actual != expected:
        raise ModelUnavailable(
            "Prüfsumme des KI-Modells stimmt nicht – Datei beschädigt oder ausgetauscht."
        )

    try:
        return loader(str(model_path))
    except Exception as exc:  # z. B. defekte GGUF-Datei trotz passender Prüfsumme
        raise ModelUnavailable("KI-Modell konnte nicht geladen werden.") from exc
