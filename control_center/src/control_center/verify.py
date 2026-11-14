#!/usr/bin/env python3
"""
verify.py – HMAC-Signaturprüfung für Telemetriedaten

Funktionen:
 - Lädt den Geräteschlüssel ausschließlich aus der Umgebungsvariable
   SENSOR_SECRET_HEX (shared.crypto.secret_env). Es gibt keinen Rückfall
   auf eine Konfigurationsdatei: Fehlt der Schlüssel, bricht die Prüfung ab.
 - Verifiziert HMAC-SHA256-Signaturen
 - Wird vom Receiver-Modul verwendet
"""

from shared.protocol.signed_csv import verify_signed_line
from shared.crypto.hmac_sha256 import verify as hmac_verify
from shared.crypto.secret_env import load_sensor_secret_hex


# --- Delegiert an shared.crypto.hmac_sha256 ---
def verify(secret_hex: str, payload_bytes: bytes, mac_hex: str) -> bool:
    """
    Überprüft eine HMAC-SHA256-Signatur.

    Parameter:
        secret_hex (str): Geheimer Schlüssel im Hex-Format.
        payload_bytes (bytes): Die originalen Nutzdaten (z. B. Telemetriedaten).
        mac_hex (str): Die empfangene HMAC-Signatur im Hex-Format.

    Rückgabe:
        bool – True, wenn die Signatur gültig ist, sonst False.
    """
    expected = payload_bytes.decode("UTF-8")
    return hmac_verify(expected, secret_hex, mac_hex)


# --- Varianten mit Schlüssel aus der Umgebung ---
def verify_with_env_key(payload_bytes: bytes, mac_hex: str) -> bool:
    """Prüft mit dem Geräteschlüssel aus SENSOR_SECRET_HEX."""
    secret = load_sensor_secret_hex()
    return verify(secret, payload_bytes, mac_hex)

def verify_csv_line_with_env_key(line: str) -> bool:
    """
    Verifiziert eine Telemetriezeile im Format:
    ts,temperature_c,humidity_pct,pressure_hpa,mode,sig

    Der Schlüssel wird aus SENSOR_SECRET_HEX geladen.
    """
    secret = load_sensor_secret_hex()
    return verify_signed_line(line, secret)

def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="Path to telemetry CSV")
    args = ap.parse_args()

    ok = 0
    bad = 0

    with open(args.csv_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("ts,"):
                continue

            if verify_csv_line_with_env_key(line):
                ok += 1
            else:
                bad += 1
                print(f"[FAIL] line {i}: {line}")

    print(f"[DONE] ok={ok} bad={bad}")


if __name__ == "__main__":
    main()
