"""KI-Analyse-Endpoint.

Bewusst `def` statt `async def`: Die Inferenz blockiert (M3 ~20 s, Intel-CPU
1–2 min). FastAPI führt sync-Endpoints in einem Threadpool aus, dadurch laufen
SSE-Stream und andere Endpoints während der Analyse weiter.

Antworten:
  200  Bericht erzeugt
  409  Analyse bewusst verweigert (keine, zu wenige oder veraltete Daten)
  429  es läuft bereits eine Analyse
  503  KI-Modell nicht verfügbar (fehlt, Prüfsumme falsch, Laden fehlgeschlagen)
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from dashboard.backend.config import ANALYSIS_RETRY_AFTER_S
from dashboard.backend.services.ai_analyst import AnalysisBusy, AnalysisUnavailable
from dashboard.backend.services.csv_reader import read_last_n

router = APIRouter()

# Mehr als genug für das 30-Minuten-Fenster bei 1 Paket/Minute;
# die zeitbasierte Auswahl passiert im prompt_builder.
PACKETS_TO_READ = 60


@router.post("/analyse")
def analyse(request: Request):
    analyst = getattr(request.app.state, "analyst", None)
    if analyst is None:
        reason = getattr(request.app.state, "analyst_error", None) or "KI-Modell nicht verfügbar."
        raise HTTPException(status_code=503, detail=reason)

    try:
        report = analyst.analyse(read_last_n(PACKETS_TO_READ))
    except AnalysisBusy as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(ANALYSIS_RETRY_AFTER_S)},
        )
    except AnalysisUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "report": report,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
