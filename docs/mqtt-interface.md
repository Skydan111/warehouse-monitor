# MQTT Interface Contract (v1)

## Ziel
Implementierung eines realen Kommunikationskanals zwischen Zonensensor (Sensorrechner) und Leitstand
über MQTT, ohne Änderungen an der bestehenden Architektur (zone_sensor/control_center/shared).

Dieses Dokument definiert Topics, Message Envelope, QoS-Regeln sowie Anti-Replay/Dedup-Verhalten.

---

## Begriffe
- Zonensensor: Sensorrechner (Raspberry Pi).
- Leitstand: Empfangs- und Verifikations-Pipeline (macOS).
- Broker: MQTT Broker (z. B. Mosquitto).
- Signed Payload: vorhandenes Format aus shared/ (HMAC-SHA256 Signatur/Verifikation).

---

## MQTT Topics (v1)

### Basispräfix
warehouse/v1/<zone_id>/...

### Uplink: Telemetry
- Topic: warehouse/v1/<zone_id>/telemetry
- Richtung: Zonensensor → Leitstand

### Downlink: Command (später)
- Topic: warehouse/v1/<zone_id>/command
- Richtung: Leitstand → Zonensensor

### Status / Presence (optional)
- Topic: warehouse/v1/<zone_id>/status

---

## QoS / Retain (Startkonfiguration)

### Telemetry
- QoS: 1
- retain: false

### Command (später)
- QoS: 1
- retain: false

### Status (optional)
- QoS: 0 oder 1
- retain: nach Bedarf (initial: false)

Begründung: QoS1 ist realistisch für IoT-Telemetrie und erzwingt korrektes Dedup-Verhalten (Duplicate Messages sind möglich).

---

## Message Format: Transport Envelope (JSON) + Signed Payload

MQTT Message Payload ist ein JSON-Objekt (UTF-8).
Der eigentliche fachliche Payload bleibt unverändert: vorhandener Signed Payload aus shared/
wird als Base64 in payload_b64 transportiert.

### Envelope Schema (v1)
Felder:
- v (string): Envelope-Version. Muss "1" sein.
- zone_id (string): Zonen-ID.
- msg_type (string): "telemetry" (später auch `"command"`).
- msg_id (string): Eindeutige Message-ID (UUID v4 empfohlen).
- ts_utc (string): UTC Timestamp (ISO-8601, z. B. `2026-01-06T15:20:00Z`).
- qos (number): QoS-Level (0/1/2). Nur für Diagnose/Logging.
- payload_b64 (string): Base64-kodiertes Signed Payload (bytes).

### Beispiel (Telemetry)
```json
{
  "v": "1",
  "zone_id": "ZONE-01",
  "msg_type": "telemetry",
  "msg_id": "b7d3b4fb-1f7a-4c4a-9b22-2d1c6f2a9e8a",
  "ts_utc": "2026-01-06T15:20:00Z",
  "qos": 1,
  "payload_b64": "BASE64_ENCODED_SIGNED_PAYLOAD_BYTES"
}
```

## Sicherheitsregeln (Application Layer)

### Verifikation (Leitstand)

Reihenfolge der Verarbeitung im Leitstand (`mqtt_subscriber.process_message`):

1. Envelope JSON validieren
   (Pflichtfelder vorhanden, `v == "1"`, `zone_id` = eigene Zone, `msg_type == "telemetry"`)
   → sonst **drop**, `malformed_packet` an den SecurityManager.

2. Paketrate der Zone prüfen: höchstens `RATE_LIMIT_MAX_MESSAGES` (Standard 10)
   in `RATE_LIMIT_WINDOW_SECONDS` (Standard 60 s), gleitendes Fenster
   → sonst **drop** ohne Dekodieren, HMAC und Ablage; gesammelte Meldung
   `rate_limited` (höchstens eine je Fenster).

3. Signed Payload aus `payload_b64` decodieren (Base64 streng, UTF-8, 6 Felder)
   → sonst `corrupt_payload` bzw. `malformed_packet` (letzteres in *rejected*).

4. Signatur/HMAC über die Logik in `shared/` verifizieren
   → sonst *rejected*, `invalid_signature`.

5. Freshness auf dem **signierten** Zeitstempel `ts`
   (± MAX_SKEW_SECONDS, Standard 120 s) → sonst **drop + Audit** (`stale`).

6. Dedup auf der **Signatur** → bereits gesehen: **drop + Audit** (`duplicate`).

7. Lockout-Check → bei aktivem Lockout *quarantine*, sonst *processed*.

---

### Anti-Replay / Dedup

- `msg_id` und `ts_utc` des Envelopes sind **nicht signiert** und dienen nur
  der Diagnose (Logs). Sie werden für Sicherheitsentscheidungen nicht verwendet.

- Der Leitstand hält einen gemeinsamen LRU-Cache der letzten N Signaturen
  (`DEDUP_CACHE_SIZE`, Standard 500) für alle Zonen.

- Fehler aus Schritt 1, 3 und 4 zählen für den Lockout.
  `rate_limited`, `stale` und `duplicate` zählen nicht (Flut darf die Zone nicht sperren;
  QoS-1-Wiederholung, Nachsenden nach Störung).

---

## Broker-Zugang und Transport Security

### Umgesetzt

- Kein anonymer Zugang (`allow_anonymous false`).
- Anmeldung mit `MQTT_USERNAME` / `MQTT_PASSWORD`, ein Benutzer je Gerät.
- Broker-ACL (`configs/mosquitto/acl`):
  - `control_center`: `warehouse/v1/+/telemetry` lesen
  - `zone-01`: `warehouse/v1/ZONE-01/telemetry` schreiben
- CONNACK 4/5 (Zugangsdaten falsch, nicht berechtigt): Client beendet sich mit Code 2.
- Wiederverbindung (`shared/comm/mqtt_reconnect.py`): Wartezeit 1 s bis 30 s;
  auch der erste Verbindungsaufbau wird wiederholt, Clients dürfen vor dem Broker starten.
  Der Subscriber setzt das Abo nach jeder Verbindung neu, der Publisher puffert
  QoS-1-Nachrichten ohne Verbindung (`QUEUED (offline)`).
- HMAC / Signed Payload bleibt immer aktiv, unabhängig von der Anmeldung.

### Offen

- TLS / mTLS (ohne TLS sind Passwörter und Payload im Netz lesbar)
- Zonen-ID in den signierten Daten (die ACL bindet nur das Topic an den Benutzer)

---

## Definition of Done (Phase 4 – Minimal)

1. Der Zonensensor publiziert Telemetry via MQTT (QoS 1).
2. Der Leitstand empfängt Nachrichten, dedupliziert sie,
   prüft Freshness und verifiziert die Signatur.
3. Integration Test startet einen eigenen Test-Broker
   (`docker-compose.mqtt.test.yml`, Port 18830, gleiche Konfiguration und ACL)
   und prüft 50–100 Nachrichten End-to-End.
4. Security Tests:
   - Tampering
   - Replay
   führen zu Drop + sauberem Log.
