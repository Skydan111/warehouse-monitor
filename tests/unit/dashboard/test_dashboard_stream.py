"""Live-Stream und Startskript des Dashboards (Abgleich 12, Fehler ohne Daten)."""
from __future__ import annotations

import re
from pathlib import Path

from dashboard.backend.routers.stream import next_event
from dashboard_helpers import make_packets

ROOT = Path(__file__).resolve().parents[3]


def test_no_data_sends_nothing_instead_of_crashing():
    event, last_ts = next_event(None, None)
    assert event is None and last_ts is None


def test_new_measurement_is_sent_once():
    packet = make_packets(1)[0]
    event, last_ts = next_event(packet, None)
    assert event is not None and '"temperature_c":5.0' in event["data"]
    assert last_ts == packet.ts

    again, still = next_event(packet, last_ts)  # gleiche Messung beim nächsten Abfragen
    assert again is None and still == packet.ts


def test_next_measurement_is_sent():
    first, second = make_packets(2)
    _, last_ts = next_event(first, None)
    event, last_ts = next_event(second, last_ts)
    assert event is not None and last_ts == second.ts


def test_start_script_binds_to_localhost_only():
    script = (ROOT / "scripts" / "dashboard_start.sh").read_text(encoding="utf-8")
    assert re.search(r'^HOST="127\.0\.0\.1"$', script, re.MULTILINE)
    assert '--host "$HOST"' in script
    assert "0.0.0.0" not in script
