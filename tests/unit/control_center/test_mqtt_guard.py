"""Frische und Duplikate auf signierten Daten (mqtt_guard)."""
from datetime import datetime, timedelta, timezone

import pytest

from control_center.mqtt_guard import DedupLRU, GuardConfig, MqttMessageGuard, normalize_signature

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
