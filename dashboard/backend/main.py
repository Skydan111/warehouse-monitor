import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dashboard.backend.config import FRONTEND_DIR, MODEL_PATH, MODEL_SHA256_PATH
from dashboard.backend.routers.analysis import router as analysis_router
from dashboard.backend.routers.stream import router as stream_router
from dashboard.backend.routers.telemetry import router as telemetry_router
from dashboard.backend.services.ai_analyst import AIAnalyst
from dashboard.backend.services.model_loader import ModelUnavailable, prepare_model

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Modell genau einmal beim Start prüfen und laden.
    # Jeder Fehler (Datei fehlt, Prüfsumme falsch, Laden scheitert) lässt das
    # Monitoring weiterlaufen; /analyse meldet dann 503 mit dem Grund.
    app.state.analyst = None
    app.state.analyst_error = None
    try:
        app.state.analyst = AIAnalyst(prepare_model(MODEL_PATH, MODEL_SHA256_PATH))
        logger.info("KI-Modell geprüft und geladen: %s", MODEL_PATH.name)
    except ModelUnavailable as e:
        app.state.analyst_error = str(e)
        logger.error("KI-Analyse deaktiviert: %s", e)
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(telemetry_router)
app.include_router(stream_router)
app.include_router(analysis_router)

# Muss nach den Routern stehen, sonst fängt "/" alle Pfade ab.
# Absoluter Pfad: funktioniert unabhängig vom Arbeitsverzeichnis.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
