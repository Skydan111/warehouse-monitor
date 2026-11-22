from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt

from shared.comm.envelope import EnvelopeV1, bytes_from_b64
from shared.comm.topics import telemetry_topic
from shared.comm.mqtt_auth import FATAL_CONNECT_RC, connect_rc_text, load_mqtt_credentials
from shared.comm.mqtt_reconnect import configure_reconnect
from shared.protocol.signed_csv import parse_signed_line, verify_signed_line
from shared.crypto.secret_env import load_sensor_secret_hex
from control_center.mqtt_guard import GuardConfig, MqttMessageGuard, RateLimitConfig, RateLimiter
from control_center import packet_pipeline
from control_center.input_modes import ingest_raw_line
from control_center.io_utils import append_line
from control_center.packet_pipeline import handle_verified_line
from control_center.security.security_manager import SecurityManager
from pathlib import Path


def _env(name: str, default: str) -> str:
    # Liest Umgebungsvariablen mit Default-Werten.
    return os.getenv(name, default)


def on_connect(client: mqtt.Client, userdata: object, flags: dict, rc: int) -> None:
    # Callback: wird bei erfolgreicher Verbindung aufgerufen.
    zone_id: str = userdata["zone_id"]  # type: ignore[index]
    topic = telemetry_topic(zone_id)

    if rc != 0:
        print(f"[control_center] MQTT-Verbindung abgelehnt rc={rc}: {connect_rc_text(rc)}", file=sys.stderr)
        if rc in FATAL_CONNECT_RC:
            # Falsche oder fehlende Zugangsdaten behebt kein erneuter Versuch.
            userdata["fatal_rc"] = rc  # type: ignore[index]
            client.disconnect()
        return

    # Wird nach JEDER (Wieder-)Verbindung aufgerufen: Abo neu setzen,
    # denn mit clean_session=True vergisst der Broker es beim Abbruch.
    print(f"[control_center] Connected. Subscribing to {topic}")
    client.subscribe(topic, qos=1)


# ------------------------------------------------------------------
# Prüfkette für eine MQTT-Nachricht (Reihenfolge wie in der Dokumentation 3.6.3)
#
#   1. Envelope lesbar, Version, Pflichtfelder, passende Zone   -> sonst malformed_packet
#   2. Paketrate der Zone (vor HMAC, ohne Plattenzugriff)        -> rate_limited (verwerfen)
#   3. Nutzlast dekodierbar, 6 Felder                            -> corrupt_payload / malformed_packet
#   4. Signatur gültig                                           -> invalid_signature
#   5. Frische: signierter Zeitstempel                           -> stale (verwerfen)
#   6. Duplikat: Signatur                                        -> duplicate (verwerfen)
#   7. Kein aktiver Lockout                                      -> sonst quarantine
#   8. Ablage in processed
#
# Fehler aus 1, 3 und 4 werden an den SecurityManager gemeldet und zählen
# für den Lockout. 2, 5 und 6 werden nur protokolliert (siehe on_dropped);
# bei 2 gesammelt, höchstens eine Meldung je Fenster.
# ------------------------------------------------------------------

OUTCOME_ACCEPTED = "accepted"
OUTCOME_QUARANTINED = "quarantined"
OUTCOME_STALE = "stale"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_INVALID_SIGNATURE = "invalid_signature"
OUTCOME_MALFORMED = "malformed_packet"
OUTCOME_CORRUPT = "corrupt_payload"
OUTCOME_RATE_LIMITED = "rate_limited"


@dataclass
class MessageContext:
    """Alles, was die Prüfkette braucht. Wird einmal beim Start gebaut."""

    zone_id: str
    secret_hex: str
    guard: MqttMessageGuard
    secman: Optional[object]
    quarantine_path: Path
    # Standardwerte 10/60 s; main() übernimmt RATE_LIMIT_* aus der Umgebung.
    rate_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(RateLimitConfig()))


def _report_failure(ctx: MessageContext, reason: str, meta: Dict[str, Any]) -> None:
    # Fehlgeschlagene Prüfung -> SecurityManager (zählt für den Lockout).
    if ctx.secman is not None and hasattr(ctx.secman, "on_verification_result"):
        ctx.secman.on_verification_result(ok=False, reason=reason, meta=meta)


def _report_dropped(ctx: MessageContext, reason: str, meta: Dict[str, Any]) -> None:
    # Gültig signiert, aber veraltet oder doppelt -> nur protokollieren.
    if ctx.secman is not None and hasattr(ctx.secman, "on_dropped"):
        ctx.secman.on_dropped(reason=reason, meta=meta)


def _reject(ctx: MessageContext, line: str, reason: str, meta: Dict[str, Any]) -> str:
    # Zeile mit Grund in REJECTED ablegen und Fehler melden.
    # REJ_PATH wird zur Laufzeit gelesen, damit Tests den Pfad umlenken können.
    append_line(packet_pipeline.REJ_PATH, line.rstrip() + f",reason={reason}")
    _report_failure(ctx, reason, meta)
    return reason


def process_message(payload: bytes, ctx: MessageContext) -> str:
    """Führt die Prüfkette für eine Nachricht aus und liefert das Ergebnis (OUTCOME_*)."""
    meta: Dict[str, Any] = {"source": "mqtt", "transport": "mqtt", "len": len(payload)}

    # 1) Envelope
    try:
        env = EnvelopeV1.from_json(payload.decode("utf-8", errors="replace"))
    except (ValueError, TypeError, AttributeError) as e:
        meta["detail"] = f"envelope: {type(e).__name__}"
        print(f"[control_center] DROP_MALFORMED envelope ({type(e).__name__})", file=sys.stderr)
        _report_failure(ctx, OUTCOME_MALFORMED, meta)
        return OUTCOME_MALFORMED

    meta["msg_id"] = env.msg_id
    meta["zone_id"] = env.zone_id
    if env.zone_id != ctx.zone_id or env.msg_type != "telemetry":
        # zone_id ist nicht signiert; diese Prüfung ersetzt keine Signatur,
        # verhindert aber widersprüchliche Envelopes auf dem Topic dieser Zone.
        meta["detail"] = "zone_or_type_mismatch"
        print(f"[control_center] DROP_MALFORMED msg_id={env.msg_id} zone_id={env.zone_id}", file=sys.stderr)
        _report_failure(ctx, OUTCOME_MALFORMED, meta)
        return OUTCOME_MALFORMED

    # 2) Paketrate je Zone – vor Base64, HMAC und jedem Schreibzugriff.
    #    zone_id ist hier bereits gleich der eigenen Zone (Schritt 1).
    decision = ctx.rate_limiter.check(env.zone_id)
    if decision.report_dropped:
        cfg = ctx.rate_limiter.config
        print(
            f"[control_center] DROP_RATE_LIMITED zone_id={env.zone_id} count={decision.report_dropped} "
            f"limit={cfg.max_messages}/{cfg.window_seconds:g}s",
            file=sys.stderr,
        )
        _report_dropped(ctx, OUTCOME_RATE_LIMITED, {
            "source": "mqtt", "transport": "mqtt", "zone_id": env.zone_id,
            "count": decision.report_dropped,
            "limit": f"{cfg.max_messages}/{cfg.window_seconds:g}s",
        })
    if not decision.allowed:
        return OUTCOME_RATE_LIMITED

    # 3) Nutzlast dekodieren
    try:
        signed_line = bytes_from_b64(env.payload_b64).decode("utf-8").strip()
    except (ValueError, UnicodeError) as e:  # binascii.Error ist ein ValueError
        meta["detail"] = f"payload: {type(e).__name__}"
        print(f"[control_center] DROP_CORRUPT msg_id={env.msg_id}", file=sys.stderr)
        _report_failure(ctx, OUTCOME_CORRUPT, meta)
        return OUTCOME_CORRUPT

    meta["packet_id"] = signed_line.split(",", 1)[0][:40]
    ingest_raw_line(signed_line)  # Eingangsspur: alles, was sich dekodieren ließ

    try:
        tsig = parse_signed_line(signed_line)
        sig_ok = verify_signed_line(signed_line, ctx.secret_hex)
    except (ValueError, TypeError) as e:
        # falsche Feldzahl, keine Zahl, Signatur mit Nicht-ASCII-Zeichen usw.
        meta["detail"] = f"signed_line: {type(e).__name__}"
        print(f"[control_center] REJECT_MALFORMED msg_id={env.msg_id}", file=sys.stderr)
        return _reject(ctx, signed_line, OUTCOME_MALFORMED, meta)

    # 4) Signatur
    if not sig_ok:
        print(f"[control_center] REJECT_BAD_SIG msg_id={env.msg_id}", file=sys.stderr)
        return _reject(ctx, signed_line, OUTCOME_INVALID_SIGNATURE, meta)

    # 5) Frische auf dem SIGNIERTEN Zeitstempel
    if not ctx.guard.is_fresh(tsig.unsigned.ts):
        print(f"[control_center] DROP_STALE msg_id={env.msg_id} ts={tsig.unsigned.ts}", file=sys.stderr)
        _report_dropped(ctx, OUTCOME_STALE, meta)
        return OUTCOME_STALE

    # 6) Duplikat anhand der SIGNATUR (erst jetzt: nur echte Nachrichten in den Speicher)
    if ctx.guard.is_duplicate(tsig.sig):
        print(f"[control_center] DROP_DUPLICATE msg_id={env.msg_id} ts={tsig.unsigned.ts}", file=sys.stderr)
        _report_dropped(ctx, OUTCOME_DUPLICATE, meta)
        return OUTCOME_DUPLICATE

    # 7) + 8) Lockout prüfen, dann processed (bzw. quarantine)
    if handle_verified_line(signed_line, secman=ctx.secman, source="mqtt", quarantine_path=ctx.quarantine_path):
        print(f"[control_center] ACCEPT msg_id={env.msg_id} ts={tsig.unsigned.ts}")
        return OUTCOME_ACCEPTED
    return OUTCOME_QUARANTINED


def on_message(client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage) -> None:
    ctx: MessageContext = userdata["ctx"]  # type: ignore[index]
    try:
        process_message(msg.payload, ctx)
    except Exception as e:
        # Unerwarteter Fehler: Nachricht wird nie übernommen (Fail-Secure).
        print(f"[control_center] ERROR topic={msg.topic} err={type(e).__name__}: {e}", file=sys.stderr)


def main() -> int:
    broker_host = _env("MQTT_BROKER_HOST", "localhost")
    broker_port = int(_env("MQTT_BROKER_PORT", "1883"))
    zone_id = _env("ZONE_ID", "ZONE-01")
    # Fail-Secure: ohne gültigen Schlüssel kein Start (kein Ersatzschlüssel).
    secret_hex = load_sensor_secret_hex()
    creds = load_mqtt_credentials()
    policy_path = _env("SECURITY_POLICY_PATH", "configs/security_policy.yaml")
    secman = SecurityManager(policy_path=policy_path)

    quarantine_path = Path("data/quarantine/telemetry.csv")
    dedup_size = int(_env("DEDUP_CACHE_SIZE", "500"))
    max_skew = int(_env("MAX_SKEW_SECONDS", "120"))
    guard = MqttMessageGuard(GuardConfig(dedup_size=dedup_size, max_skew_seconds=max_skew))
    rate_limiter = RateLimiter(RateLimitConfig(
        max_messages=int(_env("RATE_LIMIT_MAX_MESSAGES", "10")),
        window_seconds=float(_env("RATE_LIMIT_WINDOW_SECONDS", "60")),
    ))

    ctx = MessageContext(
        zone_id=zone_id,
        secret_hex=secret_hex,
        guard=guard,
        secman=secman,
        quarantine_path=quarantine_path,
        rate_limiter=rate_limiter,
    )
    userdata = {"zone_id": zone_id, "ctx": ctx, "fatal_rc": None}
    client = mqtt.Client(client_id=f"control-center-{zone_id}", userdata=userdata)
    client.username_pw_set(creds.username, creds.password)
    client.on_connect = on_connect
    client.on_message = on_message
    configure_reconnect(client, "[control_center]")

    print(
        f"[control_center] Connecting to mqtt://{broker_host}:{broker_port} "
        f"(zone_id={zone_id}, user={creds.username})"
    )
    # connect_async + retry_first_connection: Ist der Broker beim Start noch
    # nicht bereit (z. B. direkt nach docker compose up -d), wird gewartet
    # statt mit ConnectionRefusedError abzubrechen.
    client.connect_async(broker_host, broker_port, keepalive=30)
    client.loop_forever(retry_first_connection=True)

    if userdata["fatal_rc"] is not None:
        print("[control_center] Abbruch: Anmeldung am Broker fehlgeschlagen.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
