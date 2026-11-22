"""KI-Analyse: nur eine gleichzeitig (Abgleichliste 10) und Antwortcodes des Endpoints.

Ersatzmodell statt llama-cpp, Daten per monkeypatch statt aus data/.
"""
from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.backend import main
from dashboard.backend.routers import analysis
from dashboard.backend.services.ai_analyst import AIAnalyst, AnalysisBusy
from dashboard.backend.services.model_loader import ModelUnavailable
from dashboard_helpers import FakeLlm, make_packets


# ---------------------------------------------------------------- AIAnalyst

def test_second_analysis_is_rejected_while_first_runs():
    gate = threading.Event()
    llm = FakeLlm(gate=gate)
    analyst = AIAnalyst(llm)
    packets = make_packets()
    result = {}

    worker = threading.Thread(target=lambda: result.update(report=analyst.analyse(packets)))
    worker.start()
    assert llm.started.wait(timeout=5)

    with pytest.raises(AnalysisBusy):
        analyst.analyse(packets)

    gate.set()
    worker.join(timeout=5)
    assert result["report"] == "Testbericht"
    assert llm.calls == 1  # die abgewiesene Anfrage hat das Modell nie erreicht


def test_lock_is_released_after_success_and_after_refusal():
    analyst = AIAnalyst(FakeLlm())
    assert analyst.analyse(make_packets()) == "Testbericht"
    assert analyst.analyse(make_packets()) == "Testbericht"

    from dashboard.backend.services.ai_analyst import AnalysisUnavailable

    with pytest.raises(AnalysisUnavailable):
        analyst.analyse([])
    assert analyst.analyse(make_packets()) == "Testbericht"  # Sperre wieder frei


# ---------------------------------------------------------------- Endpoint

class _Analyst:
    def __init__(self, exc=None):
        self.exc = exc

    def analyse(self, packets):
        if self.exc:
            raise self.exc
        return "Bericht"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(analysis, "read_last_n", lambda n: make_packets())
    app = FastAPI()
    app.include_router(analysis.router)
    app.state.analyst = None
    app.state.analyst_error = None
    return app, TestClient(app)


def test_busy_returns_429_with_retry_after(client):
    app, c = client
    app.state.analyst = _Analyst(AnalysisBusy("Es läuft bereits eine KI-Analyse."))
    r = c.post("/analyse")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "30"
    assert "bereits" in r.json()["detail"]


def test_missing_model_returns_503_with_reason(client):
    app, c = client
    app.state.analyst_error = "Prüfsumme des KI-Modells stimmt nicht"
    r = c.post("/analyse")
    assert r.status_code == 503
    assert "Prüfsumme" in r.json()["detail"]


def test_refused_analysis_returns_409(client):
    from dashboard.backend.services.ai_analyst import AnalysisUnavailable

    app, c = client
    app.state.analyst = _Analyst(AnalysisUnavailable("veraltet"))
    assert c.post("/analyse").status_code == 409


def test_success_returns_report(client):
    app, c = client
    app.state.analyst = _Analyst()
    r = c.post("/analyse")
    assert r.status_code == 200
    assert r.json()["report"] == "Bericht"


# ---------------------------------------------------------------- Start (lifespan)

def test_startup_with_wrong_checksum_keeps_dashboard_running(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise ModelUnavailable("Prüfsumme des KI-Modells stimmt nicht – Datei beschädigt oder ausgetauscht.")

    monkeypatch.setattr(main, "prepare_model", refuse)
    monkeypatch.setattr(analysis, "read_last_n", lambda n: make_packets())
    with TestClient(main.app) as c:
        r = c.post("/analyse")
        assert r.status_code == 503
        assert "Prüfsumme" in r.json()["detail"]
        assert c.get("/telemetry/thresholds").status_code == 200  # Monitoring läuft weiter


def test_startup_with_valid_model_enables_analysis(monkeypatch):
    monkeypatch.setattr(main, "prepare_model", lambda *a, **k: FakeLlm())
    monkeypatch.setattr(analysis, "read_last_n", lambda n: make_packets())
    with TestClient(main.app) as c:
        r = c.post("/analyse")
        assert r.status_code == 200
        assert r.json()["report"] == "Testbericht"
