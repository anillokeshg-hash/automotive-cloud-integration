"""SQLite storage for the cloud side: telemetry, alerts and remote commands.

The HTTP server handles requests on several threads, so every database
operation takes a lock.
"""

import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    vin               TEXT NOT NULL,
    ts                TEXT NOT NULL,
    received_at       TEXT NOT NULL,
    speed_kmh         REAL NOT NULL,
    engine_temp_c     REAL NOT NULL,
    fuel_pct          REAL NOT NULL,
    battery_v         REAL NOT NULL,
    odometer_km       REAL NOT NULL,
    tire_pressure_psi REAL,
    lat               REAL NOT NULL,
    lon               REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_vin ON telemetry (vin, id);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vin         TEXT NOT NULL,
    type        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    opened_at   TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_vin ON alerts (vin, resolved_at);

CREATE TABLE IF NOT EXISTS commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vin          TEXT NOT NULL,
    command      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    delivered_at TEXT
);
"""

TELEMETRY_COLUMNS = ["vin", "ts", "speed_kmh", "engine_temp_c", "fuel_pct", "battery_v", "odometer_km", "tire_pressure_psi", "lat", "lon"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- telemetry ----
    def add_telemetry(self, reading: dict) -> None:
        values = [reading[c] for c in TELEMETRY_COLUMNS]
        with self.lock, self.conn:
            self.conn.execute(
                f"INSERT INTO telemetry ({', '.join(TELEMETRY_COLUMNS)}, received_at) VALUES ({', '.join('?' * (len(TELEMETRY_COLUMNS) + 1))})",
                (*values, utc_now()),
            )

    def latest(self, vin: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM telemetry WHERE vin = ? ORDER BY id DESC LIMIT 1", (vin,)).fetchone()
        return dict(row) if row else None

    def vehicle_known(self, vin: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM telemetry WHERE vin = ? LIMIT 1", (vin,)).fetchone() is not None

    def fleet(self) -> list[dict]:
        """Latest reading of every vehicle, plus its number of open alerts."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT t.*,
                          (SELECT COUNT(*) FROM alerts a WHERE a.vin = t.vin AND a.resolved_at IS NULL) AS open_alerts
                   FROM telemetry t
                   JOIN (SELECT vin, MAX(id) AS max_id FROM telemetry GROUP BY vin) m ON t.id = m.max_id
                   ORDER BY t.vin"""
            ).fetchall()
        return [dict(r) for r in rows]

    def history(self, vin: str, limit: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM telemetry WHERE vin = ? ORDER BY id DESC LIMIT ?", (vin, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---- alerts ----
    def sync_alerts(self, vin: str, findings: list[dict], event_types: set[str]) -> list[dict]:
        """Open alerts for new conditions, resolve alerts whose condition cleared.

        A condition that stays true does NOT create a new alert on every reading.
        Returns the alerts that were newly created.
        """
        now = utc_now()
        created = []
        with self.lock, self.conn:
            open_rows = {
                r["type"]: r
                for r in self.conn.execute("SELECT * FROM alerts WHERE vin = ? AND resolved_at IS NULL", (vin,))
            }
            active = {f["type"] for f in findings}
            for f in findings:
                is_event = f["type"] in event_types
                if is_event or f["type"] not in open_rows:
                    cur = self.conn.execute(
                        "INSERT INTO alerts (vin, type, severity, message, opened_at, resolved_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (vin, f["type"], f["severity"], f["message"], now, now if is_event else None),
                    )
                    created.append({"id": cur.lastrowid, "vin": vin, **f})
            for alert_type, row in open_rows.items():
                if alert_type not in active:
                    self.conn.execute("UPDATE alerts SET resolved_at = ? WHERE id = ?", (now, row["id"]))
        return created

    def alerts(self, only_open: bool = False, vin: str | None = None, limit: int = 100) -> list[dict]:
        sql, params = "SELECT * FROM alerts WHERE 1=1", []
        if only_open:
            sql += " AND resolved_at IS NULL"
        if vin:
            sql += " AND vin = ?"
            params.append(vin)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params)]

    # ---- remote commands ----
    def queue_command(self, vin: str, command: str) -> int:
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO commands (vin, command, created_at) VALUES (?, ?, ?)", (vin, command, utc_now()))
        return cur.lastrowid

    def take_pending_commands(self, vin: str) -> list[dict]:
        """Return the vehicle's pending commands and mark them delivered."""
        with self.lock, self.conn:
            rows = self.conn.execute("SELECT id, command FROM commands WHERE vin = ? AND status = 'pending' ORDER BY id", (vin,)).fetchall()
            self.conn.executemany(
                "UPDATE commands SET status = 'delivered', delivered_at = ? WHERE id = ?",
                [(utc_now(), r["id"]) for r in rows],
            )
        return [dict(r) for r in rows]
