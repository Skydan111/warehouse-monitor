# Sicherheitsmodell

## Überblick

Das Sicherheitskonzept des Warehouse Monitors schützt die Datenintegrität und -authentizität auf der Kommunikationsstrecke zwischen Zonensensor und Leitstand.

Der Fokus liegt auf:
- **Integrität** — Erkennung von Manipulationen am Payload
- **Replay-Resistenz** — Verhinderung von Wiederholungsangriffen
- **Anomalie-Erkennung** — Adaptive Reaktion auf verdächtige Muster

**Teilweise abgedeckt:**
- Zugang zum Broker: Anmeldung mit Benutzer und Passwort, Zugriffsliste je Benutzer (ohne TLS, siehe unten)

**Nicht abgedeckt:**
- Vertraulichkeit (Payload ist nicht verschlüsselt)
- Unterscheidung der Endpunkte über den HMAC-Schlüssel (Shared Secret, kein PKI)
- Transport-Security (MQTT ohne TLS in Demo-Umgebung)

---

## Bedrohungsmodell

| Bedrohung | Beschreibung | Kontrolle | Layer |
|-----------|--------------|-----------|-------|
| **Payload-Manipulation** | Angreifer ändert Telemetriedaten während der Übertragung | HMAC-SHA256 Signaturprüfung | Application |
| **Replay-Angriffe** | Angreifer sendet alte Nachrichten erneut, auch in neuem Envelope | Freshness auf signiertem `ts` + Deduplication auf der Signatur | Application |
| **Duplicate Messages** | Netzwerk/Broker sendet Nachricht mehrfach (QoS 1) | Deduplication auf der Signatur (LRU Cache) | Application |
| **Wiederholte ungültige Versuche** | Angreifer sendet kontinuierlich ungültige Nachrichten | Adaptive Security (Lockout) | Application |
| **Malformed Packets** | Beschädigte oder absichtlich fehlerhafte Nachrichten | Schema-Validierung, Fehlerbehandlung | Application |
| **Unbefugter Zugriff auf den Broker** | Fremder Client sendet oder liest mit | Anmeldung, Passwort je Gerät, ACL je Benutzer | Broker |

**Nicht adressierte Bedrohungen (Demo-Scope):**
- Man-in-the-Middle (kein TLS)
- Side-Channel-Angriffe (z.B. Timing)
- DoS auf Broker-Ebene
- Physischer Zugriff auf Zonensensor/Leitstand

---

## HMAC-SHA256 Integritätsschutz

### Kryptographisches Verfahren

**Algorithmus:** HMAC-SHA256 (RFC 2104 + FIPS 180-4)

**Schlüssel:**
- Symmetrischer Shared Secret (hexadezimal kodiert)
- Länge: empfohlen ≥ 32 Bytes (256 Bit)
- Verteilung: manuell (Demo), später: Key Management Service

**Signatur-Erzeugung (Zonensensor):**
```python
payload = "ts,temp,hum,press,mode"
signature = hmac.new(key=secret, msg=payload.encode(), digestmod=hashlib.sha256).hexdigest()
# Ausgabe: 64 Hex-Zeichen
```

**Signatur-Verifikation (Leitstand):**
```python
expected_sig = hmac.new(key=secret, msg=payload.encode(), digestmod=hashlib.sha256).hexdigest()
is_valid = hmac.compare_digest(received_sig, expected_sig)  # Constant-time
```

### Signatur-Scope

Die Signatur wird über den **kanonischen Payload** berechnet:
```
ts,temperature_c,humidity_pct,pressure_hpa,mode
```

**Nicht signiert:**
- Envelope-Felder (msg_id, ts_utc, etc.) — Transport-Layer
- Die Signatur selbst

**Kanonische Form:**
- Exakte Feldanzahl (5 Felder)
- Komma-separiert, keine Leerzeichen
- Numerische Werte mit 2 Dezimalstellen (z.B. `23.50`)

### Angriffsvektoren und Schutz

| Angriff | Schutz |
|---------|--------|
| Payload-Änderung | Signatur wird ungültig → rejected/ |
| Signatur-Entfernung | Schema-Validierung erkennt fehlendes Feld → malformed |
| Signatur-Ersetzen (ohne Schlüssel) | HMAC-Verifikation schlägt fehl → rejected/ |
| Known-Plaintext | HMAC ist resistent (One-Way-Function) |
| Timing-Angriff auf Verify | `hmac.compare_digest()` ist constant-time |

---

## Geheimnisse und Broker-Zugang

### Geräteschlüssel

- Einzige Quelle: Umgebungsvariable `SENSOR_SECRET_HEX`, lokal aus `.env` geladen (`scripts/lib/env.sh`).
- Gelesen und geprüft nur in `shared/crypto/secret_env.py`: genau 64 Hex-Zeichen (32 Byte).
- Kein Vorgabewert, kein zweiter Variablenname, kein Rückfall auf eine Konfigurationsdatei.
- Fehlt der Schlüssel oder ist er ungültig, starten Zonensensor, Subscriber und Receiver nicht. Die Pipeline legt in diesem Fall auch keine Zeile als „malformed“ ab, sondern bricht ab: Ein Konfigurationsfehler ist kein Paketfehler.
- Der Wert erscheint in keiner Meldung, keinem Log und keinem Test. Tests erzeugen je Lauf einen eigenen Schlüssel (`secrets.token_hex(32)`).

### Broker-Anmeldung und Zugriffsliste

| Einstellung | Wert |
|-------------|------|
| Anonymer Zugang | aus (`allow_anonymous false`) |
| Passwortdatei | `configs/mosquitto/passwd`, nur Hashes, nicht versioniert |
| Zugriffsliste | `configs/mosquitto/acl`, versioniert |

| Benutzer | Darf |
|----------|------|
| `control_center` | `warehouse/v1/+/telemetry` lesen, nichts senden |
| `zone-01` | nur `warehouse/v1/ZONE-01/telemetry` schreiben, nichts lesen |

Die Clients lesen `MQTT_USERNAME` und `MQTT_PASSWORD` aus der Umgebung (`shared/comm/mqtt_auth.py`), ohne Vorgabewerte. Lehnt der Broker die Anmeldung ab (CONNACK 4 oder 5), beenden sich Subscriber und Publisher mit Code 2. Ein erneuter Versuch würde falsche Zugangsdaten nicht beheben.

Ohne TLS gehen Benutzername und Passwort im Klartext über das Netz. Die Anmeldung schützt vor zufälligem Zugriff, nicht vor einem Angreifer, der das Netz mitschneidet.

### Erzeugen und Wechseln der Geheimnisse

`scripts/mqtt_provision.sh <IP des Leitstands>` läuft auf dem Leitstand und erzeugt in einem Schritt:

- einen neuen Geräteschlüssel,
- je ein zufälliges Passwort für `control_center` und `zone-01`,
- `.env` (Leitstand), `.env.zone-01` (für den Zonensensor) und die gehashte Passwortdatei.

Alle Dateien erhalten die Rechte 600. Kein Wert wird ausgegeben. Vorhandene Dateien werden nur mit `--force` überschrieben; damit ist das Skript zugleich der Ablauf für einen Schlüsselwechsel.

### Kompromittierter Schlüssel

Bis November 2026 stand ein Geräteschlüssel im Repository: in `configs/control_center.json`, als Vorgabewert in `scripts/control_center_start.sh` und in einem Test. Er bleibt im Git-Verlauf sichtbar. Löschen aus der aktuellen Version reicht deshalb nicht. Der Schlüssel gilt als bekannt und wurde auf allen Geräten durch einen neuen ersetzt.

---

## Prüfkette und Anti-Replay im MQTT-Empfang

### Reihenfolge

Implementiert in `control_center/mqtt_subscriber.py` (`process_message`):

| Schritt | Prüfung | Bei Fehler | Zählt für Lockout |
|---------|---------|------------|-------------------|
| 1 | Envelope: JSON, `v == "1"`, Pflichtfelder, `zone_id` = eigene Zone, `msg_type == "telemetry"` | verwerfen, `malformed_packet` | ja (0,8) |
| 2 | Paketrate der Zone | *(eigener Branch)* | – |
| 3 | Nutzlast: Base64 (streng), UTF-8 | verwerfen, `corrupt_payload` | ja (0,6) |
| 3 | Nutzlast: 6 Felder, Zahlen lesbar | *rejected*, `malformed_packet` | ja (0,8) |
| 4 | HMAC-Signatur | *rejected*, `invalid_signature` | ja (1,0) |
| 5 | Frische: **signierter** `ts` ± 120 s | verwerfen, Audit `stale` | nein |
| 6 | Duplikat: **Signatur** bereits gesehen | verwerfen, Audit `duplicate` | nein |
| 7 | Lockout aktiv | *quarantine* | – |
| 8 | – | *processed* | – |

Jede dekodierbare Nutzlast wird vor Schritt 4 unverändert in `data/raw/` abgelegt (Eingangsspur für die Forensik).

### Warum auf signierten Daten

Die Felder des Envelopes (`msg_id`, `ts_utc`, `zone_id`) sind nicht signiert. Eine frühere Version prüfte Frische an `ts_utc` und Duplikate an `msg_id`. Ein Angreifer konnte eine mitgeschnittene Nutzlast in einen neuen Envelope mit neuer `msg_id` und neuer Zeit legen; beide Prüfungen hätten nichts bemerkt.

Jetzt werden der Zeitstempel `ts` aus der signierten Nutzlast und die Signatur selbst verwendet. Beides kann ein Angreifer nicht ändern, ohne dass Schritt 4 fehlschlägt.

### Warum die Signatur vor Frische und Duplikat

- Der Duplikat-Speicher soll nur echte Nachrichten enthalten. Sonst könnte ein Angreifer ihn mit erfundenen Einträgen füllen und echte Einträge verdrängen.
- Eine gefälschte, zugleich veraltete Nachricht wird als `invalid_signature` gezählt und nicht als harmloses `stale` verworfen.

### Duplikat-Speicher

- Schlüssel: Signatur der Nutzlast, normalisiert wie bei der Signaturprüfung (Leerzeichen am Rand entfernt). Eine Schreibvariante, die die Prüfung besteht, gilt damit nicht als neue Nachricht.
- Ein gemeinsamer Speicher für alle Zonen: `zone_id` ist nicht signiert und darf den Speicher nicht aufteilen.
- Größe: 500 Einträge (`DEDUP_CACHE_SIZE`), der älteste fällt heraus. Bei einer Nachricht pro Minute reicht das weit über das Frischefenster von 120 Sekunden hinaus.

### Warum `stale` und `duplicate` nicht für den Lockout zählen

- Bei QoS 1 darf der Broker eine Nachricht mehrfach zustellen.
- Nach einer Verbindungsstörung sendet der Zonensensor ältere Messwerte nach.

Beides ist kein Angriff. Würden diese Fälle zählen, könnte eine kurze Netzstörung einen Lockout auslösen. Sie werden über `SecurityManager.on_dropped()` im Security- und Audit-Log festgehalten.

### Beispiel

1. Angreifer schneidet eine gültige Nachricht mit (signierter `ts` = 10:00:05).
2. 10:00:40: Er sendet dieselbe Nutzlast in einem neuen Envelope (neue `msg_id`, neue Zeit).
   - Signatur: gültig
   - Frische: OK (35 s)
   - Duplikat: **Signatur bereits gesehen → verworfen**
3. 10:10:00: Erneuter Versuch.
   - Frische: **signierter `ts` 10 Minuten alt → verworfen**

---

## Adaptive Security (Lockout-Mechanismus)

### Motivation

Bei normalem Betrieb sind gelegentliche fehlerhafte Nachrichten akzeptabel (Netzwerkfehler, Hardware-Glitches).

**Aber:** Wiederholte, konsistente Fehler deuten auf:
- Aktiven Angriff (z.B. Brute-Force auf Signatur)
- Kompromittierter Zonensensor
- Fehlkonfiguration

**Adaptive Security** reagiert dynamisch auf Fehlermuster.

---

### Gleitendes Zeitfenster

Der Leitstand hält Ereignisprotokoll der letzten N Sekunden (z.B. 60s):

```python
events = [
    {"ts": "10:00:05", "type": "invalid_signature"},
    {"ts": "10:00:08", "type": "malformed_packet"},
    {"ts": "10:00:12", "type": "invalid_signature"},
    ...
]
```

**Trim-Logik:**
- Alte Events (älter als Zeitfenster) werden entfernt
- Nur aktuelle Events zählen für Anomalie-Erkennung

---

### Gewichtete Fehlertypen

Nicht alle Fehler sind gleich kritisch:

| Event Type | Gewicht | Begründung |
|------------|---------|------------|
| `invalid_signature` | 1.0 | Kritisch: HMAC ungültig → mögliche Manipulation |
| `malformed_packet` | 0.5 | Medium: Beschädigte Nachricht, weniger verdächtig |
| `duplicate` | 0.1 | Niedrig: QoS-1-Artefakt, meist harmlos |

**Konfiguration:** `configs/security_policy.yaml`

---

### Lockout-Schwellwerte

**1. Consecutive Fails (Folge-Fehler):**
```yaml
consecutive_fails_lockout: 5
```
Wenn ≥ 5 aufeinanderfolgende Fehler → **LOCKOUT**

**2. Weighted Ratio (Fehlerquote):**
```yaml
lockout_threshold: 0.7  # 70%
```
Wenn gewichtete Fehlerquote ≥ 70% → **LOCKOUT**

**Berechnung:**
```python
total_weight = sum(weight(event) for event in window)
total_events = len(window)
ratio = total_weight / total_events

if ratio >= lockout_threshold:
    trigger_lockout()
```

---

### Lockout-Verhalten

**Bei aktivem Lockout:**

1. **Vor HMAC-Verifikation:**
   - Nachricht wird direkt in `data/quarantine/` geschrieben
   - Keine teure HMAC-Berechnung
   - Audit-Log: `lockout_quarantine`

2. **Nach erfolgreicher Verifikation:**
   - Lockout wird **nicht** aufgehoben
   - Erfordert manuellen Eingriff oder Timeout

**Ziel:**
- Schutz vor Resource-Exhaustion (HMAC-Berechnung ist teuer)
- Signal an Operator: "Anomalie erkannt, prüfen erforderlich"

---

### Beispiel-Szenario

**Timeline:**

| Zeit | Event | Action | Lockout? |
|------|-------|--------|----------|
| 10:00:00 | valid | processed | Nein |
| 10:00:05 | invalid_signature (1.0) | rejected | Nein |
| 10:00:10 | invalid_signature (1.0) | rejected | Nein |
| 10:00:15 | invalid_signature (1.0) | rejected | Nein |
| 10:00:20 | invalid_signature (1.0) | rejected | Nein |
| 10:00:25 | invalid_signature (1.0) | rejected | **JA** (5 consecutive) |
| 10:00:30 | valid (aber Lockout aktiv) | **quarantine** | JA |

**Ergebnis:** Nach dem 5. Fehler wird Lockout aktiviert. Selbst gültige Nachrichten landen in Quarantäne.

---

## Konfiguration

Sicherheitsparameter sind in `configs/security_policy.yaml` definiert:

```yaml
security:
  enabled: true

  window_seconds: 60
  consecutive_fails_lockout: 5
  lockout_threshold: 0.7

  weights:
    invalid_signature: 1.0
    malformed_packet: 0.5
    duplicate: 0.1
```

**Vorteile:**
- Keine Hardcoding im Code
- Anpassbar ohne Code-Änderung
- Testbar mit unterschiedlichen Policies

---

## Audit-Logging

Alle sicherheitsrelevanten Ereignisse werden protokolliert:

### Human-Readable Log (`logs/security.log`)

```
2026-02-25 15:20:05 [WARNING] Invalid signature detected. msg_id=abc123
2026-02-25 15:20:12 [CRITICAL] Lockout triggered. consecutive_fails=5
```

### Machine-Readable Log (`logs/security_audit.jsonl`)

```json
{"ts": "2026-02-25T15:20:05Z", "event": "invalid_signature", "msg_id": "abc123", "zone_id": "ZONE-01"}
{"ts": "2026-02-25T15:20:12Z", "event": "lockout_triggered", "reason": "consecutive_fails", "count": 5}
```

**Stripped Metadata:**
- Keine Secrets (HMAC-Schlüssel)
- Keine vollständigen Payloads
- Nur Event-Typ, IDs, Timestamps

---

## Grenzen & Annahmen

### Demo-Umgebung

**Nicht produktionsreif:**
- Ein gemeinsamer Schlüssel für alle Geräte, im Klartext in einer lokalen `.env`
- Schlüsselwechsel nur von Hand (`scripts/mqtt_provision.sh --force`), kein regelmäßiger Wechsel
- Kein Hardware Security Module (HSM)
- Keine TLS/mTLS auf MQTT, Broker-Passwörter im Klartext auf dem Netz
- Kein zentrales Secret Management

### HMAC-Limitierungen

**Integrität, nicht Vertraulichkeit:**
- HMAC schützt gegen Manipulation
- Payload ist **nicht verschlüsselt**
- Telemetriedaten sind im Klartext übertragen

**Shared Secret:**
- Symmetrisch: Beide Seiten kennen Schlüssel
- Kompromittierung einer Seite → gesamtes System unsicher
- Keine Forward Secrecy

### Replay-Schutz

**Time-Sync-Abhängigkeit:**
- Freshness Check erfordert synchrone Uhren (NTP auf Zonensensor und Leitstand)
- Falsche Uhrzeit auf einem Gerät → echte Nachrichten werden als `stale` verworfen

**Neustart des Leitstands:**
- Der Duplikat-Speicher liegt nur im Arbeitsspeicher
- Nach einem Neustart wird eine Nachricht, deren signierter `ts` jünger als 120 Sekunden ist, erneut angenommen

**Nachgesendete Messwerte:**
- Messwerte, die nach einer Verbindungsstörung mehr als 120 Sekunden alt ankommen, werden verworfen
- Sie bleiben in der CSV-Datei auf dem Zonensensor erhalten und können von dort manuell ausgewertet werden

**Weitere Eingänge:**
- Der Weg über die Kommandozeile (`receiver.py --file`) prüft nur die Signatur, nicht Frische und Duplikate

### Adaptive Security

**False Positives:**
- Netzwerkprobleme könnten Lockout triggern
- Erfordert manuellen Eingriff

**Keine automatische Recovery:**
- Lockout bleibt bis manuell gelöst oder System-Neustart

---

## Security Testing

Sicherheitseigenschaften werden in `tests/integration/security/` geprüft:

### Test Coverage

- **Tampering** — Manipulierte Payloads werden rejected
- **Replay** — Duplicate msg_id wird erkannt und gedropped
- **Malformed** — Fehlerhafte Envelope/Payload führen zu Fehlermeldung
- **Lockout** — Wiederholte Fehler triggern Quarantine

### Test-Strategie

- Unit-Tests: Crypto-Funktionen (sign/verify)
- Integration-Tests: End-to-End mit MQTT
- Security-Tests: Angriffssimulationen

---

## Empfehlungen für Produktionsumgebung

### Kurzfristig (Quick Wins)

1. **TLS für MQTT**
   - Broker mit TLS-Zertifikat
   - Client-seitige Zertifikatsvalidierung

2. **Secret Management**
   - Umgesetzt: Secrets nur aus Umgebungsvariablen bzw. lokaler `.env`, Wechsel per Skript
   - Offen: Schlüsseltresor, regelmäßige Rotation, Schlüssel je Gerät

3. **Monitoring & Alerting**
   - Security-Events in SIEM
   - Alarm bei Lockout

### Mittelfristig

4. **mTLS (Mutual TLS)**
   - Client-Zertifikate für Zonensensoren
   - Broker prüft Client-Identität

5. **Rate Limiting**
   - Broker-seitige Limits pro zone_id
   - Schutz vor Flooding

6. **Key Rotation**
   - Regelmäßiger HMAC-Schlüsselwechsel
   - Unterstützung für Schlüssel-Versioning

### Langfristig

7. **PKI-basierte Authentifizierung**
   - Asymmetrische Kryptographie (z.B. Ed25519)
   - Zertifikatsketten

8. **Payload-Verschlüsselung**
   - AES-GCM für Vertraulichkeit
   - Zusätzlich zu HMAC (Encrypt-then-MAC)

9. **Hardware Security Module (HSM)**
   - Schlüssel verlassen nie HSM
   - FIPS 140-2/3 Compliance

---

*Letzte Aktualisierung: November 2026*
