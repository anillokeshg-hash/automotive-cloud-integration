"""One-command demo: starts the cloud, drives 3 vehicles, sends a remote command, prints results.

    python demo.py
"""

import logging
import threading

from cloud_client import CloudClient
from server import make_server
from service import CloudService
from storage import Store
from vehicle_simulator import simulate

KEY = "demo-key"


def table(headers, rows):
    rows = [[str(c) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    service = CloudService(Store(":memory:"))
    server = make_server(service, KEY, port=0)  # port 0 = any free port
    url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Cloud API running at {url}\n")

    client = CloudClient(url, KEY)

    def queue_command(tick):
        if tick == 15:  # a fleet manager flashes AUTO-0001's lights remotely
            status, body = client.api("POST", "/api/vehicles/AUTO-0001/commands", {"command": "flash_lights"})
            print(f"[tick 15] queued remote command 'flash_lights' for AUTO-0001 -> HTTP {status}")

    print("Driving 3 vehicles for 60 ticks...")
    received = simulate(client, ticks=60, interval=0.02, on_tick=queue_command)
    for vin, command in received:
        print(f"  {vin} received and executed: {command['command']}")

    print("\nFLEET STATUS (latest reading per vehicle)")
    _, fleet = client.api("GET", "/api/vehicles")
    table(
        ["VIN", "Speed", "Temp C", "Fuel %", "Tire psi", "Odometer", "Open alerts"],
        [(v["vin"], v["speed_kmh"], v["engine_temp_c"], v["fuel_pct"], v["tire_pressure_psi"], round(v["odometer_km"]), v["open_alerts"]) for v in fleet],
    )

    print("\nALERT HISTORY (oldest first)")
    _, alerts = client.api("GET", "/api/alerts?limit=50")
    table(
        ["VIN", "Type", "Severity", "State", "Message"],
        [(a["vin"], a["type"], a["severity"], "OPEN" if a["resolved_at"] is None else "resolved", a["message"]) for a in reversed(alerts)],
    )

    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
