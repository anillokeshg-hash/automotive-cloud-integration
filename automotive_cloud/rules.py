"""Alert rules: turn raw telemetry into meaningful alerts."""

from datetime import datetime

OVERHEAT_C = 105
LOW_FUEL_PCT = 10
LOW_BATTERY_V = 11.8  # a healthy 12V battery is ~12.4V+ when the engine is off
SPEED_LIMIT_KMH = 120
LOW_TIRE_PSI = 26
HARSH_BRAKE_DROP_KMH = 40  # speed lost between two readings...
HARSH_BRAKE_WINDOW_S = 15  # ...that are at most this many seconds apart

# Events happen once and are not "open"/"resolved" conditions
EVENT_TYPES = {"HARSH_BRAKING"}


def _seconds_between(earlier_iso: str, later_iso: str) -> float:
    return (datetime.fromisoformat(later_iso) - datetime.fromisoformat(earlier_iso)).total_seconds()


def evaluate(reading: dict, prev: dict | None = None) -> list[dict]:
    """Return the alerts that apply to this reading.

    `prev` is the vehicle's previous reading (used to detect harsh braking).
    Each finding is {"type", "severity", "message"}.
    """
    found = []

    if reading["engine_temp_c"] > OVERHEAT_C:
        found.append({"type": "OVERHEAT", "severity": "critical", "message": f"Engine temperature {reading['engine_temp_c']} C is above {OVERHEAT_C} C"})
    if reading["fuel_pct"] < LOW_FUEL_PCT:
        found.append({"type": "LOW_FUEL", "severity": "warning", "message": f"Fuel level {reading['fuel_pct']}% is below {LOW_FUEL_PCT}%"})
    if reading["battery_v"] < LOW_BATTERY_V:
        found.append({"type": "LOW_BATTERY", "severity": "warning", "message": f"Battery voltage {reading['battery_v']} V is below {LOW_BATTERY_V} V"})
    if reading["speed_kmh"] > SPEED_LIMIT_KMH:
        found.append({"type": "SPEEDING", "severity": "warning", "message": f"Speed {reading['speed_kmh']} km/h is above {SPEED_LIMIT_KMH} km/h"})

    tire = reading.get("tire_pressure_psi")
    if tire is not None and tire < LOW_TIRE_PSI:
        found.append({"type": "LOW_TIRE", "severity": "warning", "message": f"Tire pressure {tire} psi is below {LOW_TIRE_PSI} psi"})

    if prev is not None:
        drop = prev["speed_kmh"] - reading["speed_kmh"]
        gap = _seconds_between(prev["ts"], reading["ts"])
        if drop >= HARSH_BRAKE_DROP_KMH and 0 <= gap <= HARSH_BRAKE_WINDOW_S:
            found.append({"type": "HARSH_BRAKING", "severity": "info", "message": f"Speed dropped {drop:.0f} km/h in {gap:.0f}s"})

    return found
