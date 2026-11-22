"""Statistik-Endpoint: Zeitraum wird mitgeliefert, ohne Daten keine Exception."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.backend.routers import telemetry
from dashboard_helpers import make_packets


def _client():
    app = FastAPI()
    app.include_router(telemetry.router)
    return TestClient(app)


def test_stats_report_count_of_measurements(monkeypatch):
    monkeypatch.setattr(telemetry, "read_last_n", lambda n: make_packets(12, temp=4.0))
    body = _client().get("/telemetry/stats").json()
    assert body["count"] == 12
    assert body["temperature"] == {"min": 4.0, "max": 4.0, "avg": 4.0}


def test_stats_without_data(monkeypatch):
    monkeypatch.setattr(telemetry, "read_last_n", lambda n: [])
    body = _client().get("/telemetry/stats").json()
    assert body["count"] == 0
    assert body["humidity"] == {"min": None, "max": None, "avg": None}


def test_history_limit_returns_last_measurements(monkeypatch):
    calls = []
    monkeypatch.setattr(telemetry, "read_last_n", lambda n: calls.append(n) or make_packets(n))
    body = _client().get("/telemetry/history?limit=30").json()
    assert calls == [30] and len(body) == 30


def test_history_limit_is_validated():
    assert _client().get("/telemetry/history?limit=0").status_code == 422
