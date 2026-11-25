# Automotive Cloud Integration

A simple base project showing how connected cars talk to the cloud:

```
 Vehicles (simulated)                         Cloud
 ┌──────────────────┐   POST /api/telemetry   ┌─────────────────────────────────────┐
 │ speed, temp,     │ ──────────────────────> │ validate -> store -> alert rules    │
 │ fuel, battery,   │                         │          │                          │
 │ tires, GPS ...   │ <────────────────────── │   remote commands (lock, flash...)  │
 │ (buffers data    │   commands in response  └──────────┬──────────────────────────┘
 │  when offline)   │                                    │ GET /api/vehicles, /api/alerts
 └──────────────────┘                              Fleet dashboard (/)
```

Pure Python 3.10+ standard library - **nothing to install**.

## What it does

| Part | What it does |
|------|--------------|
| **Telemetry ingestion** | vehicles POST JSON readings; the cloud validates every field and stores them |
| **Alert rules** | overheating, low fuel, low battery, speeding, low tire pressure, harsh braking |
| **Smart alerts** | an alert opens once when a problem starts and resolves when it clears (no flood of duplicates) |
| **Remote commands** | a fleet manager queues `lock_doors`, `unlock_doors`, `flash_lights` or `precondition_cabin`; the vehicle receives it in the response to its next reading, exactly once |
| **Offline buffering** | the vehicle-side client keeps readings when there is no signal and sends them in order when it reconnects |
| **Fleet dashboard** | live web page: status of every vehicle and recent alerts |
| **Security basics** | API key on every `/api` call, strict input validation, request size limit, no HTML injection in the dashboard |

## Project structure

```
automotive_cloud/
├── server.py             # HTTP API + dashboard hosting (the "cloud")
├── service.py            # business logic: ingest, alerts, commands
├── rules.py              # alert thresholds and detection
├── validation.py         # checks incoming data
├── storage.py            # SQLite storage
├── dashboard.html        # live fleet dashboard
├── cloud_client.py       # vehicle -> cloud client with offline buffer
├── vehicle_simulator.py  # 3 simulated vehicles with different problems
├── demo.py               # everything in one command
├── config.py             # settings (env variables)
└── test_automotive.py    # tests
```

## Quick start

**One-command demo** (starts the cloud, drives 3 vehicles, sends a remote command, prints a summary):
```bash
python demo.py
```

**Run it as separate pieces** (like a real deployment) in two terminals:
```bash
python server.py                              # terminal 1 - the cloud, then open http://127.0.0.1:8080
python vehicle_simulator.py --ticks 60        # terminal 2 - the cars
```

**Queue a remote command** by hand:
```bash
curl -X POST http://127.0.0.1:8080/api/vehicles/AUTO-0001/commands \
     -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
     -d '{"command": "flash_lights"}'
```

**Tests:** `python -m unittest -v`

## API

| Method & path | Purpose |
|---------------|---------|
| `POST /api/telemetry` | send one reading; response lists new alerts and pending commands |
| `GET /api/vehicles` | latest status of every vehicle |
| `GET /api/vehicles/<vin>/telemetry?limit=50` | recent history |
| `GET /api/alerts?open=1&vin=...&limit=...` | alerts |
| `POST /api/vehicles/<vin>/commands` | queue a remote command |
| `GET /health` | liveness check |

A telemetry reading looks like:
```json
{"vin": "AUTO-0001", "timestamp": "2026-11-25T10:00:00Z", "speed_kmh": 72.5,
 "engine_temp_c": 91, "fuel_pct": 63.2, "battery_v": 12.6, "odometer_km": 18250.4,
 "tire_pressure_psi": 32, "lat": 17.385, "lon": 78.4867}
```

## Before using this for anything real

This is a learning base. For production you would add:
- **HTTPS/TLS** (the server speaks plain HTTP) and a proper API gateway.
- **Per-vehicle credentials** (certificates or tokens) instead of one shared key. The dashboard currently takes the key from the page URL (`/?key=...`), which is fine on your own computer but not for shared use - use real user login.
- **Command safety**: confirm who may unlock a car, log every command, and have the vehicle verify commands.
- A production database and rate limiting.

## Mapping to real cloud services

| This project | AWS | Azure | Google Cloud |
|---|---|---|---|
| `POST /api/telemetry` | IoT Core | IoT Hub | Pub/Sub + IoT partner |
| `service.py` + `rules.py` | Lambda / Kinesis | Functions / Stream Analytics | Cloud Functions / Dataflow |
| `storage.py` | Timestream / DynamoDB | Data Explorer / Cosmos DB | Bigtable / BigQuery |
| remote commands | IoT Jobs / shadow | Device twin / direct methods | IoT partner / Pub/Sub |
| `dashboard.html` | QuickSight / Grafana | Power BI / Grafana | Looker / Grafana |
