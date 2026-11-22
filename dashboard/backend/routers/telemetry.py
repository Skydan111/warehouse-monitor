from fastapi import APIRouter, Query
from dashboard.backend.services.csv_reader import read_all, read_latest, read_last_n
from dashboard.backend.config import THRESHOLDS


router = APIRouter(prefix="/telemetry")

# Zeitraum der Statistik: die letzten STATS_PACKETS Messwerte
STATS_PACKETS = 60

@router.get("/history")
def get_history(limit: int | None = Query(default=None, ge=1, le=10000)):
    """Alle Messwerte oder nur die letzten `limit` (Vorbefüllung der Diagramme)."""
    return read_last_n(limit) if limit else read_all()

@router.get("/latest")
def get_latest():
    return read_latest()

@router.get("/stats")
def get_stats():
    packets = read_last_n(STATS_PACKETS)
    return {
        "count": len(packets),  # tatsächliche Anzahl, für die Beschriftung im Dashboard
        "temperature": _summary([p.temperature_c for p in packets]),
        "humidity":    _summary([p.humidity_pct for p in packets]),
        "pressure":    _summary([p.pressure_hpa for p in packets]),
    }


def _summary(values: list[float]) -> dict:
    """Min/Max/Avg; ohne Daten None-Werte statt Exception (fail-secure)."""
    if not values:
        return {"min": None, "max": None, "avg": None}
    return {"min": min(values), "max": max(values), "avg": round(sum(values) / len(values), 2)}


@router.get("/thresholds")
def get_thresholds():
    return THRESHOLDS

