"""Gemeinsame Hilfen für die Dashboard-Tests (ohne echtes Modell, ohne data/)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from dashboard.backend.models import TelemetryPacket


def make_packets(n: int = 15, now: datetime | None = None, temp: float = 5.0) -> list[TelemetryPacket]:
    """n Pakete im Minutenabstand, das letzte genau bei `now`."""
    now = now or datetime.now(timezone.utc)
    return [
        TelemetryPacket(
            ts=now - timedelta(minutes=n - 1 - i),
            temperature_c=temp,
            humidity_pct=50.0,
            pressure_hpa=980.0,
            mode="NOMINAL",
            sig="00" * 32,
        )
        for i in range(n)
    ]


class FakeLlm:
    """Ersetzt llama_cpp.Llama: liefert festen Text, kann auf ein Signal warten."""

    def __init__(self, text: str = "Testbericht", gate: threading.Event | None = None):
        self.text = text
        self.gate = gate
        self.started = threading.Event()
        self.calls = 0

    def __call__(self, prompt, **kwargs):
        self.calls += 1
        self.started.set()
        if self.gate is not None:
            self.gate.wait(timeout=5)
        return {"choices": [{"text": f"  {self.text}  "}]}
