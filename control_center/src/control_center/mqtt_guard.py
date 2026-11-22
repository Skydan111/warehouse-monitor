"""
mqtt_guard.py – Frische- und Duplikatprüfung für den MQTT-Empfang.

Beide Prüfungen arbeiten ausschließlich auf SIGNIERTEN Daten:
  • Frische:   Zeitstempel `ts` aus der signierten Nutzlast (nicht `ts_utc` des Envelopes)
  • Duplikat:  die HMAC-Signatur selbst (nicht `msg_id` des Envelopes)

Grund: Die Felder des Envelopes sind nicht signiert. Ein Angreifer kann eine
mitgeschnittene Nutzlast in einen neuen Envelope mit neuer msg_id und neuer
Zeit legen. Zeitstempel und Signatur der Nutzlast kann er dagegen nicht
ändern, ohne dass die Signaturprüfung fehlschlägt.

Die Prüfungen dürfen erst NACH erfolgreicher Signaturprüfung aufgerufen
werden. Sonst könnte ein Angreifer den Duplikat-Speicher mit erfundenen
Einträgen füllen und echte Einträge verdrängen.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class GuardConfig:
    # Duplikat-Speicher: Anzahl der zuletzt gesehenen Signaturen.
    dedup_size: int = 500
    # Frische: erlaubte Abweichung des signierten Zeitstempels von der Systemzeit (Sekunden).
    max_skew_seconds: int = 120


class DedupLRU:
    """Begrenzter Speicher der zuletzt gesehenen Schlüssel; der älteste fällt heraus."""

    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._lru: "OrderedDict[str, None]" = OrderedDict()

    def seen_before(self, key: str) -> bool:
        # Prüft, ob der Schlüssel bereits gesehen wurde, und merkt ihn sich.
        if key in self._lru:
            self._lru.move_to_end(key)
            return True
        self._lru[key] = None
        if len(self._lru) > self._max_size:
            self._lru.popitem(last=False)
        return False

    def __len__(self) -> int:
        return len(self._lru)


class MqttMessageGuard:
    def __init__(self, cfg: GuardConfig) -> None:
        self._cfg = cfg
        # Ein gemeinsamer Speicher für alle Zonen: zone_id steht nur im
        # (unsignierten) Envelope und darf deshalb nicht Teil des Schlüssels sein.
        self._dedup = DedupLRU(cfg.dedup_size)

    def is_fresh(self, signed_ts: str, now: Optional[datetime] = None) -> bool:
        """True, wenn der signierte Zeitstempel höchstens max_skew_seconds von der Systemzeit abweicht."""
        try:
            msg_time = _parse_utc(signed_ts)
        except ValueError:
            return False
        now = now or datetime.now(timezone.utc)
        skew = abs((now - msg_time).total_seconds())
        return skew <= self._cfg.max_skew_seconds

    def is_duplicate(self, signature: str) -> bool:
        """True, wenn diese Signatur bereits angenommen wurde. Merkt sich neue Signaturen."""
        return self._dedup.seen_before(normalize_signature(signature))


def normalize_signature(signature: str) -> str:
    """
    Gleiche Schreibweise wie bei der Signaturprüfung (dort: strip()).
    Sonst könnte eine Variante mit Leerzeichen die Prüfung bestehen,
    aber als neuer Eintrag im Duplikat-Speicher gelten.
    """
    return signature.strip().lower()


def _parse_utc(ts_utc: str) -> datetime:
    # Parst ISO-8601 mit 'Z' oder Offset; ohne Zeitzone wird UTC angenommen.
    s = ts_utc.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
