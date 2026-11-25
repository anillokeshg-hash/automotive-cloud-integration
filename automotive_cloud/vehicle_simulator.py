"""Simulated connected vehicles that stream telemetry to the cloud.

Each vehicle has a "scenario" so the cloud has something interesting to detect:

    AUTO-0001  braker      normal driving, then one harsh braking event
    AUTO-0002  overheater  starts with low fuel, then its engine overheats and recovers
    AUTO-0003  speeder     drives too fast for a while, and has a slow tire leak

Run against a running server:
    python vehicle_simulator.py --url http://127.0.0.1:8080 --key dev-key --ticks 60 --interval 0.5
"""

import argparse
import logging
import math
import random
import time
from datetime import datetime, timezone

from cloud_client import CloudClient

log = logging.getLogger("simulator")


class SimVehicle:
    def __init__(self, vin, scenario, rng, lat, lon, fuel=75.0, odometer=24000.0):
        self.vin, self.scenario, self.rng = vin, scenario, rng
        self.lat, self.lon, self.heading = lat, lon, rng.uniform(0, 6.28)
        self.speed, self.fuel, self.odometer = 70.0, fuel, odometer
        self.temp, self.battery, self.tire = 90.0, 12.6, 32.0

    def step(self, tick: int) -> dict:
        """Advance one tick (the simulation treats one tick as one minute of driving)."""
        target = 75 + self.rng.uniform(-8, 8)
        if self.scenario == "speeder" and 10 <= tick <= 25:
            target = 135 + self.rng.uniform(-5, 5)
        self.speed = max(0.0, self.speed + (target - self.speed) * 0.5)
        if self.scenario == "braker":
            if tick == 19:
                self.speed = 95.0
            elif tick == 20:
                self.speed = 30.0  # sudden stop

        burn = 0.35 if self.scenario == "overheater" else 0.2
        self.fuel = max(0.0, self.fuel - burn * self.rng.uniform(0.8, 1.2))
        if self.scenario == "overheater" and tick == 48:
            self.fuel = 65.0  # the driver stops and refuels, so the LOW_FUEL alert resolves

        if self.scenario == "overheater" and 30 <= tick <= 44:
            self.temp += 2.5
        else:
            self.temp += (90 - self.temp) * 0.4
        self.temp += self.rng.uniform(-0.5, 0.5)

        if self.scenario == "speeder":
            self.tire -= 0.25
        self.battery = 12.6 + self.rng.uniform(-0.1, 0.1)

        self.odometer += self.speed / 60
        self.heading += self.rng.gauss(0, 0.1)
        self.lat += math.cos(self.heading) * self.speed * 1e-5
        self.lon += math.sin(self.heading) * self.speed * 1e-5

        return {
            "vin": self.vin,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "speed_kmh": round(self.speed, 1),
            "engine_temp_c": round(self.temp, 1),
            "fuel_pct": round(self.fuel, 1),
            "battery_v": round(self.battery, 2),
            "odometer_km": round(self.odometer, 1),
            "tire_pressure_psi": round(self.tire, 1),
            "lat": round(self.lat, 5),
            "lon": round(self.lon, 5),
        }


def make_fleet(seed: int = 7) -> list[SimVehicle]:
    rng = random.Random(seed)
    return [
        SimVehicle("AUTO-0001", "braker", rng, lat=17.3850, lon=78.4867, fuel=80, odometer=18250),
        SimVehicle("AUTO-0002", "overheater", rng, lat=12.9716, lon=77.5946, fuel=14, odometer=42100),
        SimVehicle("AUTO-0003", "speeder", rng, lat=19.0760, lon=72.8777, fuel=60, odometer=9800),
    ]


def simulate(client: CloudClient, ticks: int = 60, interval: float = 0.5, seed: int = 7, on_tick=None) -> list[tuple[str, dict]]:
    """Drive the fleet for `ticks` steps. Returns every (vin, command) the vehicles received.

    on_tick(tick) is an optional hook (the demo uses it to queue a remote command).
    """
    fleet = make_fleet(seed)
    received = []
    for tick in range(ticks):
        for vehicle in fleet:
            for command in client.send(vehicle.step(tick)):
                log.info("%s executing remote command: %s", vehicle.vin, command["command"])
                received.append((vehicle.vin, command))
        if on_tick:
            on_tick(tick)
        time.sleep(interval)
    return received


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated connected vehicles")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--key", default="dev-key")
    parser.add_argument("--ticks", type=int, default=60)
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between ticks")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    client = CloudClient(args.url, args.key)
    simulate(client, args.ticks, args.interval)
    log.info("Simulation finished (%d reading(s) still buffered)", len(client.buffer))


if __name__ == "__main__":
    main()
