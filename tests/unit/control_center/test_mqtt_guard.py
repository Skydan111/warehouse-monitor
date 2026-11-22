"""Frische, Duplikate und Paketrate beim MQTT-Empfang (mqtt_guard)."""
from datetime import datetime, timedelta, timezone

import pytest

from control_center.mqtt_guard import (
    DedupLRU,
    GuardConfig,
    MqttMessageGuard,
    RateLimitConfig,
    RateLimiter,
    normalize_signature,
)

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


def guard(**kw) -> MqttMessageGuard:
    return MqttMessageGuard(GuardConfig(**kw))


# ---------------------------------------------------------------- Frische

@pytest.mark.parametrize("offset_s", [0, 119, -119, 120, -120])
def test_timestamp_within_window_is_fresh(offset_s):
    ts = (NOW + timedelta(seconds=offset_s)).isoformat()
    assert guard().is_fresh(ts, now=NOW) is True


@pytest.mark.parametrize("offset_s", [121, -121, 3600, -86400])
def test_timestamp_outside_window_is_stale(offset_s):
    # Auch Zeitstempel aus der Zukunft werden abgelehnt.
    ts = (NOW + timedelta(seconds=offset_s)).isoformat()
    assert guard().is_fresh(ts, now=NOW) is False


@pytest.mark.parametrize(
    "ts",
    [
        "2026-11-22T12:00:30Z",                     # Z-Schreibweise
        "2026-11-22T12:00:30+00:00",                # Offset
        "2026-11-22T12:00:30.123456+00:00",         # Mikrosekunden (Zonensensor)
        "2026-11-22T13:00:30+01:00",                # andere Zeitzone, gleiche Zeit
    ],
)
def test_timestamp_formats_are_understood(ts):
    assert guard().is_fresh(ts, now=NOW) is True


@pytest.mark.parametrize("ts", ["", "kein-datum", "2026-13-45T99:99:99Z"])
def test_unreadable_timestamp_is_not_fresh(ts):
    assert guard().is_fresh(ts, now=NOW) is False


def test_window_is_configurable():
    ts = (NOW - timedelta(seconds=30)).isoformat()
    assert guard(max_skew_seconds=10).is_fresh(ts, now=NOW) is False


# ---------------------------------------------------------------- Duplikate

def test_first_signature_is_new_second_is_duplicate():
    g = guard()
    sig = "ab" * 32
    assert g.is_duplicate(sig) is False
    assert g.is_duplicate(sig) is True


def test_signature_variants_count_as_same_entry():
    # Die Signaturprüfung entfernt Leerzeichen am Rand. Eine solche Variante
    # darf deshalb nicht als neue Nachricht durchgehen.
    g = guard()
    sig = "ab" * 32
    assert g.is_duplicate(sig) is False
    assert g.is_duplicate(f"  {sig}\n") is True
    assert g.is_duplicate(sig.upper()) is True


def test_dedup_does_not_depend_on_zone():
    # zone_id ist nicht signiert und darf den Speicher nicht aufteilen.
    g = guard()
    sig = "cd" * 32
    assert g.is_duplicate(sig) is False
    assert g.is_duplicate(sig) is True


def test_dedup_memory_is_bounded():
    lru = DedupLRU(max_size=3)
    for key in ["a", "b", "c", "d"]:
        lru.seen_before(key)
    assert len(lru) == 3
    assert lru.seen_before("a") is False  # ältester Eintrag ist herausgefallen


def test_normalize_signature():
    assert normalize_signature("  ABcd \n") == "abcd"


# ---------------------------------------------------------------- Paketrate (RateLimiter)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _limiter(max_messages: int = 10, window: float = 60.0, **kw) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    return RateLimiter(RateLimitConfig(max_messages=max_messages, window_seconds=window, **kw), clock=clock), clock


def test_rate_limit_lets_through_up_to_limit_then_drops():
    rl, _ = _limiter()
    results = [rl.check("ZONE-01").allowed for _ in range(15)]
    assert results == [True] * 10 + [False] * 5


def test_rate_limit_window_slides():
    rl, clock = _limiter(max_messages=2, window=60)
    assert rl.check("Z").allowed          # t=1000
    clock.t += 30
    assert rl.check("Z").allowed          # t=1030
    assert not rl.check("Z").allowed      # Fenster voll
    clock.t += 30                         # t=1060: erster Eintrag fällt heraus
    assert rl.check("Z").allowed
    assert not rl.check("Z").allowed


def test_dropped_messages_do_not_extend_the_window():
    # Eine Flut darf echte Messwerte nach Ablauf des Fensters nicht weiter blockieren.
    rl, clock = _limiter(max_messages=1, window=60)
    assert rl.check("Z").allowed
    for _ in range(50):
        clock.t += 1
        assert not rl.check("Z").allowed
    clock.t += 10                         # 60 s nach der letzten DURCHGELASSENEN Nachricht
    assert rl.check("Z").allowed


def test_zones_are_limited_independently():
    rl, _ = _limiter(max_messages=1)
    assert rl.check("ZONE-01").allowed
    assert not rl.check("ZONE-01").allowed
    assert rl.check("ZONE-02").allowed


def test_drops_are_reported_at_most_once_per_window():
    rl, clock = _limiter(max_messages=1, window=60)
    rl.check("Z")
    report_times = []
    reports = []
    for _ in range(300):                  # Flut über 300 s, 1 Nachricht pro Sekunde
        clock.t += 1
        d = rl.check("Z")
        if d.report_dropped:
            report_times.append(clock.t)
            reports.append(d.report_dropped)
    clock.t += 60
    final = rl.check("Z")                 # Flut vorbei: nächste echte Nachricht bringt den Rest
    assert final.allowed and final.report_dropped > 0
    report_times.append(clock.t)
    reports.append(final.report_dropped)

    assert reports[0] == 1                # Beginn der Flut wird sofort gemeldet
    gaps = [b - a for a, b in zip(report_times, report_times[1:])]
    assert all(g >= 60 for g in gaps)     # nie mehr als eine Meldung je Fenster
    # Nichts geht verloren: alle Verwerfungen sind gezählt
    allowed_during_flood = 300 // 60      # alle 60 s wird das Fenster wieder frei
    assert sum(reports) == 300 - allowed_during_flood


def test_no_report_without_drops():
    rl, _ = _limiter()
    assert all(rl.check("Z").report_dropped == 0 for _ in range(10))


def test_number_of_tracked_zones_is_bounded():
    rl, _ = _limiter(max_zones=3)
    for i in range(10):
        rl.check(f"ZONE-{i:02d}")
    assert len(rl._zones) == 3


@pytest.mark.parametrize("kw", [{"max_messages": 0}, {"window_seconds": 0}, {"max_zones": 0}])
def test_invalid_rate_limit_config_is_refused(kw):
    with pytest.raises(ValueError):
        RateLimitConfig(**kw)
