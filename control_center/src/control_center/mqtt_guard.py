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

Ausnahme ist die Paketrate (RateLimiter): Sie wird bewusst VOR der
Signaturprüfung angewendet. Bei einer Flut soll der Leitstand weder HMAC
rechnen noch auf die Platte schreiben. Ihr Speicher ist je Zone auf
max_messages Zeitstempel begrenzt und kann deshalb nicht überlaufen.
"""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Deque, Optional


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


@dataclass(frozen=True)
class RateLimitConfig:
    # Höchstens max_messages Nachrichten je Zone in window_seconds (gleitendes Fenster).
    # Der Zonensensor sendet 1 Nachricht pro Minute; 10/60 s lässt 10-fachen Spielraum.
    max_messages: int = 10
    window_seconds: float = 60.0
    # Obergrenze für die Anzahl gemerkter Zonen (Speicher bleibt begrenzt).
    max_zones: int = 64

    def __post_init__(self) -> None:
        if self.max_messages < 1 or self.window_seconds <= 0 or self.max_zones < 1:
            raise ValueError("RateLimitConfig: Werte müssen positiv sein")


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    # Anzahl verworfener Nachrichten, die JETZT gesammelt protokolliert werden
    # sollen (0 = nichts protokollieren).
    report_dropped: int = 0


@dataclass
class _ZoneState:
    accepted: Deque[float]
    dropped_pending: int = 0
    next_report_at: float = 0.0


class RateLimiter:
    """
    Begrenzung der Paketrate je Zone mit gleitendem Fenster.

    - Gezählt werden nur durchgelassene Nachrichten. Verworfene verlängern das
      Fenster nicht: Nach dem Ende einer Flut kommen echte Messwerte sofort
      wieder durch.
    - Verworfene Nachrichten werden gesammelt gemeldet, höchstens eine Meldung
      je Fenster: die erste Verwerfung sofort (Beginn der Flut sichtbar), danach
      die seitdem gezählten frühestens ein Fenster später – bei der nächsten
      verworfenen oder durchgelassenen Nachricht. So bleibt das Log auch bei
      einer Flut klein, und keine Verwerfung bleibt ungezählt.
    """

    def __init__(self, cfg: RateLimitConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self._cfg = cfg
        self._clock = clock
        self._zones: "OrderedDict[str, _ZoneState]" = OrderedDict()

    @property
    def config(self) -> RateLimitConfig:
        return self._cfg

    def check(self, zone_id: str, now: Optional[float] = None) -> RateDecision:
        now = self._clock() if now is None else now
        st = self._state(zone_id)

        border = now - self._cfg.window_seconds
        while st.accepted and st.accepted[0] <= border:
            st.accepted.popleft()

        allowed = len(st.accepted) < self._cfg.max_messages
        if allowed:
            st.accepted.append(now)
        else:
            st.dropped_pending += 1

        return RateDecision(allowed=allowed, report_dropped=self._take_report(st, now))

    def _take_report(self, st: _ZoneState, now: float) -> int:
        # Gesammelte Verwerfungen abgeben, wenn seit der letzten Meldung ein Fenster vergangen ist.
        if st.dropped_pending == 0 or now < st.next_report_at:
            return 0
        count, st.dropped_pending = st.dropped_pending, 0
        st.next_report_at = now + self._cfg.window_seconds
        return count

    def _state(self, zone_id: str) -> _ZoneState:
        st = self._zones.get(zone_id)
        if st is None:
            st = _ZoneState(accepted=deque(maxlen=self._cfg.max_messages))
            self._zones[zone_id] = st
            if len(self._zones) > self._cfg.max_zones:
                self._zones.popitem(last=False)
        else:
            self._zones.move_to_end(zone_id)
        return st


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
