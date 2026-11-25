"""Check incoming vehicle data before it is stored.

Never trust data from the network: a faulty sensor or a malicious client could
send anything. Bad payloads are rejected with a clear message.
"""

import math
import re
from datetime import datetime, timezone

VIN_PATTERN = re.compile(r"[A-Za-z0-9-]{5,32}")

# field -> (minimum, maximum)
REQUIRED_FIELDS = {
    "speed_kmh": (0, 300),
    "engine_temp_c": (-40, 200),
    "fuel_pct": (0, 100),
    "battery_v": (0, 30),
    "odometer_km": (0, 5_000_000),
    "lat": (-90, 90),
    "lon": (-180, 180),
}
OPTIONAL_FIELDS = {"tire_pressure_psi": (0, 80)}


def validate_vin(vin) -> str:
    if not isinstance(vin, str) or not VIN_PATTERN.fullmatch(vin):
        raise ValueError("vin must be 5-32 letters, digits or hyphens")
    return vin.upper()


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_telemetry(payload) -> dict:
    """Return a clean telemetry dict, or raise ValueError listing every problem."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    problems = []
    clean = {}

    try:
        clean["vin"] = validate_vin(payload.get("vin"))
    except ValueError as err:
        problems.append(str(err))

    for name, (low, high) in REQUIRED_FIELDS.items():
        value = payload.get(name)
        if not _is_number(value):
            problems.append(f"{name} is required and must be a number")
        elif not low <= value <= high:
            problems.append(f"{name} must be between {low} and {high}")
        else:
            clean[name] = value

    for name, (low, high) in OPTIONAL_FIELDS.items():
        value = payload.get(name)
        if value is None:
            clean[name] = None
        elif not _is_number(value) or not low <= value <= high:
            problems.append(f"{name} must be a number between {low} and {high}")
        else:
            clean[name] = value

    # Timestamp: optional ISO-8601; stored in UTC. Defaults to "now".
    raw_ts = payload.get("timestamp")
    if raw_ts is None:
        clean["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        try:
            parsed = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            clean["ts"] = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            problems.append("timestamp must be an ISO-8601 date-time")

    if problems:
        raise ValueError("; ".join(problems))
    return clean
