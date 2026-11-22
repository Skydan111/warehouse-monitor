from pathlib import Path

THRESHOLDS = {
    "temperature_c": {"min": 2.0, "max": 8.0},
    "humidity_pct":  {"min": 30.0, "max": 70.0},
    "pressure_hpa":  {"min": 940.0, "max": 1020.0},
}
LOCAL_TZ = "Europe/Berlin"
BUSINESS_HOURS = (6, 20)      # Betriebszeit 06:00–20:00 Lokalzeit
ANALYSIS_WINDOW_MIN = 30      # Analysezeitraum wie im Trainingsdatensatz
MAX_DATA_AGE_MIN = 5          # älter → keine Analyse (fail-secure)
MIN_DATA_POINTS = 10          # weniger → keine Analyse (Modell kennt nur 30/120 Punkte)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "dashboard" / "frontend"
MODEL_PATH = PROJECT_ROOT / "models" / "warehouse_model.gguf"
# Erwartete SHA-256 der Modelldatei (versioniert, kein Geheimnis);
# MODEL_SHA256 in der Umgebung hat Vorrang.
MODEL_SHA256_PATH = MODEL_PATH.with_name(MODEL_PATH.name + ".sha256")
# Hinweis an den Client bei 429: nach so vielen Sekunden erneut versuchen
ANALYSIS_RETRY_AFTER_S = 30