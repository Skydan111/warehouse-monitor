#!/usr/bin/env python3
"""
Zonensensor-Logger – Erfasst Sensordaten (BME280) und schreibt signierte Telemetrie in CSV.

Liest Konfiguration aus configs/zone_sensor.yaml und signiert jeden Datensatz mit HMAC-SHA256.
"""
import csv, time, pathlib, yaml
from .sensors.bme280 import BME280Reader
from shared.protocol.signed_csv import format_signed_line
from shared.crypto.secret_env import load_sensor_secret_hex

HERE = pathlib.Path(__file__).resolve().parent

def write_header_if_needed(path):
    if not path.exists() or path.stat().st_size == 0:
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts","temperature_c","humidity_pct","pressure_hpa","mode","sig"])

def main():
    CFG = yaml.safe_load(open(HERE.parents[2] / "configs" / "zone_sensor.yaml", "r"))

    CSV_PATH = pathlib.Path(CFG["csv_path"])
    if not CSV_PATH.is_absolute():
        CSV_PATH = (HERE.parents[2] / CSV_PATH).resolve()
    else:
        CSV_PATH = CSV_PATH.resolve()
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    sensor = BME280Reader()
    write_header_if_needed(CSV_PATH)
    interval = int(CFG["interval_sec"])
    # Fail-Secure: ohne gültigen Schlüssel kein Start (kein Ersatzschlüssel).
    secret_hex = load_sensor_secret_hex()

    print(f"[ZONE_SENSOR] logging to {CSV_PATH} every {interval}s ... Ctrl+C to stop")
    while True:
        telemetry_unsigned = sensor.read()

        line = format_signed_line(telemetry_unsigned, secret_hex)   # ts,temp,hum,press,mode,sig
        row = line.split(",")

        with open(CSV_PATH, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)

        print("[ZONE_SENSOR]", ",".join(row[:5]), "->", row[5][:8])
        time.sleep(interval)

if __name__ == "__main__":
    main()
