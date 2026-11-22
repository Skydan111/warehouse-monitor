"""Frontend lädt nichts von fremden Servern und kennzeichnet KI-Berichte."""
from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[3] / "dashboard" / "frontend"
FILES = ["index.html", "app.js", "style.css"]


def test_no_external_resources():
    for name in FILES:
        text = (FRONTEND / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), f"externe Adresse in {name}"


def test_referenced_vendor_files_exist():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"]+)"', html) + re.findall(r'url\("([^"]+)"\)', css)
    assert refs
    for ref in (r for r in refs if not r.startswith("data:")):
        assert (FRONTEND / ref).is_file(), ref


def test_ai_report_is_labelled():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "KI-generiert" in js
    assert "innerHTML" not in js  # Modelltext nur als Text ins DOM


def test_ui_language_is_german():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert '<html lang="de">' in html
    for english in ["Temperature", "Humidity", "Pressure", "NOMINAL", "Waiting for data"]:
        assert english not in html
