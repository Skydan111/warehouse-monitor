"""
Prüfkette des MQTT-Empfangs ohne Broker (mqtt_subscriber.process_message).

Belegt:
 - Replay mit neuem Envelope wird erkannt (Abgleichliste 15),
 - Frische wird am signierten Zeitstempel geprüft, nicht am Envelope,
 - Fehler aus dem MQTT-Empfang erreichen den SecurityManager und REJECTED (Abgleichliste 4),
 - Reihenfolge: Envelope → Signatur → Frische → Duplikat → Lockout (Abgleichliste 6).

Alle Dateien liegen in tmp_path; das echte data/-Verzeichnis bleibt unberührt.
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from control_center import input_modes, mqtt_subscriber as sub, packet_pipeline
from control_center.mqtt_guard import GuardConfig, MqttMessageGuard
from control_center.security.security_manager import SecurityManager
from shared.comm.envelope import EnvelopeV1, b64_from_bytes
from shared.protocol.signed_csv import format_signed_line
from shared.protocol.telemetry_csv import TelemetryUnsigned

ROOT = Path(__file__).resolve().parents[3]
ZONE = "ZONE-01"


# ---------------------------------------------------------------- Hilfen

@pytest.fixture
def paths(monkeypatch, tmp_path):
    p = {
        "raw": tmp_path / "raw.csv",
        "processed": tmp_path / "processed.csv",
        "rejected": tmp_path / "rejected.csv",
        "quarantine": tmp_path / "quarantine.csv",
        "audit": tmp_path / "audit.jsonl",
        "seclog": tmp_path / "security.log",
    }
    monkeypatch.setattr(input_modes, "RAW_PATH", p["raw"])
    monkeypatch.setattr(packet_pipeline, "PROC_PATH", p["processed"])
    monkeypatch.setattr(packet_pipeline, "REJ_PATH", p["rejected"])
    return p


@pytest.fixture
def secret():
    return secrets.token_hex(32)


@pytest.fixture
def secman(paths):
    sm = SecurityManager(
        str(ROOT / "configs" / "security_policy.yaml"),
        security_log_path=str(paths["seclog"]),
        audit_log_path=str(paths["audit"]),
    )
    yield sm
    sm._audit_fp.close()


@pytest.fixture
def ctx(paths, secret, secman):
    return sub.MessageContext(
        zone_id=ZONE,
        secret_hex=secret,
        guard=MqttMessageGuard(GuardConfig()),
        secman=secman,
        quarantine_path=paths["quarantine"],
    )


_counter = iter(range(10_000))


def signed_line(secret: str, ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    # Wie der Zonensensor: Zeitstempel mit Mikrosekunden, Messwerte variieren.
    u = TelemetryUnsigned(ts=ts.isoformat(), temperature_c=5.0 + next(_counter) / 100,
                          humidity_pct=50.0, pressure_hpa=1000.0, mode="sim")
    return format_signed_line(u, secret)


def envelope(line: str, zone_id: str = ZONE, msg_id: str | None = None, ts_utc: str | None = None) -> bytes:
    env = EnvelopeV1(
        v="1", zone_id=zone_id, msg_type="telemetry",
        msg_id=msg_id or str(uuid.uuid4()),
        ts_utc=ts_utc or EnvelopeV1.now_utc_iso(),
        qos=1, payload_b64=b64_from_bytes(line.encode("utf-8")),
    )
    return env.to_json().encode("utf-8")


def lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("ts,")]


def audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def tamper(line: str) -> str:
    # Messwert ändern, Signatur behalten.
    parts = line.split(",")
    parts[1] = "99.99"
    return ",".join(parts)


# ---------------------------------------------------------------- Normalfall

def test_valid_message_is_accepted(ctx, paths, secret):
    line = signed_line(secret)
    assert sub.process_message(envelope(line), ctx) == sub.OUTCOME_ACCEPTED
    assert lines(paths["processed"]) == [line]
    assert lines(paths["raw"]) == [line]
    assert lines(paths["rejected"]) == []


# ---------------------------------------------------------------- Replay

def test_replay_with_new_envelope_is_dropped(ctx, paths, secret):
    # Abgleichliste 15: gleiche signierte Nutzlast, neue msg_id, neue Sendezeit.
    line = signed_line(secret)
    assert sub.process_message(envelope(line, msg_id="original"), ctx) == sub.OUTCOME_ACCEPTED

    replay = envelope(line, msg_id="replay", ts_utc=EnvelopeV1.now_utc_iso())
    assert sub.process_message(replay, ctx) == sub.OUTCOME_DUPLICATE
    assert lines(paths["processed"]) == [line]


def test_replay_with_whitespace_in_signature_is_dropped(ctx, paths, secret):
    # Die Signaturprüfung toleriert Leerzeichen am Rand; der Duplikat-Speicher muss es auch.
    line = signed_line(secret)
    assert sub.process_message(envelope(line), ctx) == sub.OUTCOME_ACCEPTED
    payload, sig = line.rsplit(",", 1)
    assert sub.process_message(envelope(f"{payload}, {sig}"), ctx) == sub.OUTCOME_DUPLICATE
    assert len(lines(paths["processed"])) == 1


def test_old_signed_timestamp_in_fresh_envelope_is_stale(ctx, paths, secret):
    # Envelope behauptet "jetzt", die signierte Messung ist 10 Minuten alt.
    old = signed_line(secret, ts=datetime.now(timezone.utc) - timedelta(minutes=10))
    assert sub.process_message(envelope(old, ts_utc=EnvelopeV1.now_utc_iso()), ctx) == sub.OUTCOME_STALE
    assert lines(paths["processed"]) == []


def test_fresh_signed_timestamp_in_old_envelope_is_accepted(ctx, paths, secret):
    # Gegenprobe: Das Alter des Envelopes spielt keine Rolle mehr.
    line = signed_line(secret)
    assert sub.process_message(envelope(line, ts_utc="2020-01-01T00:00:00Z"), ctx) == sub.OUTCOME_ACCEPTED


def test_future_signed_timestamp_is_stale(ctx, secret):
    line = signed_line(secret, ts=datetime.now(timezone.utc) + timedelta(minutes=10))
    assert sub.process_message(envelope(line), ctx) == sub.OUTCOME_STALE


# ---------------------------------------------------------------- Fehler -> SecurityManager / REJECTED

def test_invalid_signature_goes_to_rejected_and_security_manager(ctx, paths, secret):
    bad = tamper(signed_line(secret))
    assert sub.process_message(envelope(bad), ctx) == sub.OUTCOME_INVALID_SIGNATURE

    rejected = lines(paths["rejected"])
    assert len(rejected) == 1 and rejected[0].endswith(",reason=invalid_signature")
    events = [e for e in audit(paths["audit"]) if e["event"] == "verify_result"]
    assert events[-1]["ok"] is False and events[-1]["reason"] == "invalid_signature"
    assert lines(paths["processed"]) == []


def test_wrong_key_is_invalid_signature(ctx, secret):
    foreign = signed_line(secrets.token_hex(32))
    assert sub.process_message(envelope(foreign), ctx) == sub.OUTCOME_INVALID_SIGNATURE


def test_five_invalid_signatures_over_mqtt_trigger_lockout(ctx, paths, secret, secman):
    # Abgleichliste 4: Angriff über MQTT löst den Lockout aus.
    for _ in range(5):
        sub.process_message(envelope(tamper(signed_line(secret))), ctx)
    assert secman.is_locked()
    assert any(e["event"] == "lockout_enabled" for e in audit(paths["audit"]))

    # Während des Lockouts: gültige Nachricht -> quarantine, nicht processed.
    good = signed_line(secret)
    assert sub.process_message(envelope(good), ctx) == sub.OUTCOME_QUARANTINED
    assert lines(paths["processed"]) == []
    assert lines(paths["quarantine"])[0].startswith(good)


@pytest.mark.parametrize(
    "payload",
    [
        b"kein json",
        b"[1, 2, 3]",
        b'{"v": "2"}',
        b'{"v": "1", "zone_id": "ZONE-01"}',
        json.dumps({"v": "1", "zone_id": ZONE, "msg_type": "telemetry", "msg_id": "x",
                    "ts_utc": "t", "qos": "eins", "payload_b64": ""}).encode(),
    ],
)
def test_broken_envelope_is_malformed_and_reported(ctx, paths, payload):
    assert sub.process_message(payload, ctx) == sub.OUTCOME_MALFORMED
    assert audit(paths["audit"])[-1]["reason"] == "malformed_packet"
    assert lines(paths["processed"]) == []


def test_envelope_for_other_zone_is_malformed(ctx, paths, secret):
    line = signed_line(secret)
    assert sub.process_message(envelope(line, zone_id="ZONE-02"), ctx) == sub.OUTCOME_MALFORMED
    assert lines(paths["processed"]) == []


def test_payload_that_is_not_base64_is_corrupt(ctx, paths):
    raw = json.dumps({"v": "1", "zone_id": ZONE, "msg_type": "telemetry", "msg_id": "x",
                      "ts_utc": EnvelopeV1.now_utc_iso(), "qos": 1, "payload_b64": "@@@"}).encode()
    assert sub.process_message(raw, ctx) == sub.OUTCOME_CORRUPT
    assert audit(paths["audit"])[-1]["reason"] == "corrupt_payload"


@pytest.mark.parametrize("line", ["nur,drei,felder", "a,b,c,d,e,f,g", "2026-11-22T12:00:00Z,x,1,2,sim,sig"])
def test_wrong_field_count_or_values_is_malformed_in_rejected(ctx, paths, line):
    assert sub.process_message(envelope(line), ctx) == sub.OUTCOME_MALFORMED
    assert lines(paths["rejected"])[0].endswith(",reason=malformed_packet")


# ---------------------------------------------------------------- Reihenfolge

def test_signature_is_checked_before_freshness(ctx, secret):
    # Gefälscht UND veraltet: Ergebnis muss "invalid_signature" sein, damit der
    # Angriff für den Lockout zählt und nicht als harmloses "stale" verschwindet.
    old_bad = tamper(signed_line(secret, ts=datetime.now(timezone.utc) - timedelta(hours=1)))
    assert sub.process_message(envelope(old_bad), ctx) == sub.OUTCOME_INVALID_SIGNATURE


def test_forged_messages_never_enter_duplicate_memory(ctx, secret):
    # Gleiche gefälschte Nachricht zweimal: beide Male invalid_signature, nie "duplicate".
    bad = tamper(signed_line(secret))
    assert sub.process_message(envelope(bad), ctx) == sub.OUTCOME_INVALID_SIGNATURE
    assert sub.process_message(envelope(bad), ctx) == sub.OUTCOME_INVALID_SIGNATURE


def test_stale_message_does_not_enter_duplicate_memory(ctx, secret):
    guard = ctx.guard
    old = signed_line(secret, ts=datetime.now(timezone.utc) - timedelta(minutes=10))
    sub.process_message(envelope(old), ctx)
    assert len(guard._dedup) == 0


# ---------------------------------------------------------------- Kein falscher Lockout

def test_duplicates_and_stale_do_not_trigger_lockout(ctx, paths, secret, secman):
    # QoS 1 darf Nachrichten mehrfach zustellen; nach einer Störung kommen alte Werte nach.
    line = signed_line(secret)
    sub.process_message(envelope(line), ctx)
    for _ in range(10):
        assert sub.process_message(envelope(line), ctx) == sub.OUTCOME_DUPLICATE
    old = signed_line(secret, ts=datetime.now(timezone.utc) - timedelta(minutes=10))
    for _ in range(10):
        assert sub.process_message(envelope(old), ctx) == sub.OUTCOME_STALE

    assert not secman.is_locked()
    dropped = [e["reason"] for e in audit(paths["audit"]) if e["event"] == "dropped"]
    assert dropped.count("duplicate") == 10 and dropped.count("stale") == 10


def test_on_message_never_raises(ctx):
    class Msg:
        topic = "warehouse/v1/ZONE-01/telemetry"
        payload = b"\xff\xfe kaputt"

    sub.on_message(None, {"ctx": ctx}, Msg())  # darf keine Ausnahme werfen
