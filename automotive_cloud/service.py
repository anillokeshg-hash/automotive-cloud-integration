"""Cloud business logic (no HTTP in here, so it is easy to test)."""

import rules
from storage import Store
from validation import validate_telemetry, validate_vin

ALLOWED_COMMANDS = {"lock_doors", "unlock_doors", "flash_lights", "precondition_cabin"}


class CloudService:
    def __init__(self, store: Store):
        self.store = store

    def ingest(self, payload) -> dict:
        """Validate, store, evaluate alert rules, hand back any pending commands.

        Raises ValueError for bad payloads.
        """
        reading = validate_telemetry(payload)
        previous = self.store.latest(reading["vin"])
        self.store.add_telemetry(reading)
        findings = rules.evaluate(reading, previous)
        new_alerts = self.store.sync_alerts(reading["vin"], findings, rules.EVENT_TYPES)
        commands = self.store.take_pending_commands(reading["vin"])
        return {"stored": True, "new_alerts": new_alerts, "commands": commands}

    def fleet(self) -> list[dict]:
        return self.store.fleet()

    def history(self, vin: str, limit: int = 50) -> list[dict]:
        return self.store.history(validate_vin(vin), max(1, min(limit, 500)))

    def alerts(self, only_open: bool = False, vin: str | None = None, limit: int = 100) -> list[dict]:
        vin = validate_vin(vin) if vin else None
        return self.store.alerts(only_open, vin, max(1, min(limit, 500)))

    def queue_command(self, vin: str, command: str) -> int:
        """Queue a remote command. ValueError = bad input, LookupError = unknown vehicle."""
        vin = validate_vin(vin)
        if command not in ALLOWED_COMMANDS:
            raise ValueError(f"command must be one of: {', '.join(sorted(ALLOWED_COMMANDS))}")
        if not self.store.vehicle_known(vin):
            raise LookupError(f"unknown vehicle {vin}")
        return self.store.queue_command(vin, command)
