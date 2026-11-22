"""Live-Stream der Messwerte (Server-Sent Events).

Alle POLL_SECONDS wird die letzte Messung gelesen, gesendet wird aber nur eine
neue Messung (anderer Zeitstempel). Ohne Daten wird nichts gesendet; der
Stream wartet, statt abzubrechen.
"""
import asyncio
from datetime import datetime

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from dashboard.backend.models import TelemetryPacket
from dashboard.backend.services.csv_reader import read_latest

router = APIRouter(prefix="/stream")

POLL_SECONDS = 5


def next_event(
    latest: TelemetryPacket | None, last_ts: datetime | None
) -> tuple[dict | None, datetime | None]:
    """Ereignis für eine neue Messung, sonst None. Liefert den neuen Stand zurück."""
    if latest is None or latest.ts == last_ts:
        return None, last_ts
    return {"data": latest.model_dump_json()}, latest.ts


@router.get("/telemetry")
async def stream_telemetry():
    async def event_generator():
        last_ts = None
        while True:
            event, last_ts = next_event(read_latest(), last_ts)
            if event is not None:
                yield event
            await asyncio.sleep(POLL_SECONDS)

    return EventSourceResponse(event_generator())
