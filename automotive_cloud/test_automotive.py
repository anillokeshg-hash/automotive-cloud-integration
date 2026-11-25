"""Unit and integration tests - run with:  python -m unittest -v"""

import json
import threading
import unittest
import urllib.error
import urllib.request

import rules
from cloud_client import CloudClient
from server import make_server
from service import CloudService
from storage import Store
from validation import validate_telemetry

KEY = "test-key"


def reading(**overrides):
    base = {
        "vin": "TEST-0001",
        "timestamp": "2026-11-25T10:00:00+00:00",
        "speed_kmh": 60,
        "engine_temp_c": 90,
        "fuel_pct": 50,
        "battery_v": 12.6,
        "odometer_km": 1000,
        "tire_pressure_psi": 32,
        "lat": 17.4,
        "lon": 78.4,
    }
    base.update(overrides)
    return base


class ValidationTests(unittest.TestCase):
    def test_valid_reading_is_cleaned(self):
        clean = validate_telemetry(reading(vin="test-0001"))
        self.assertEqual(clean["vin"], "TEST-0001")
        self.assertEqual(clean["ts"], "2026-11-25T10:00:00+00:00")

    def test_naive_and_z_timestamps_become_utc(self):
        self.assertEqual(validate_telemetry(reading(timestamp="2026-11-25T10:00:00Z"))["ts"], "2026-11-25T10:00:00+00:00")
        self.assertEqual(validate_telemetry(reading(timestamp="2026-11-25T10:00:00"))["ts"], "2026-11-25T10:00:00+00:00")

    def test_rejects_bad_values_and_reports_all_problems(self):
        with self.assertRaises(ValueError) as ctx:
            validate_telemetry(reading(speed_kmh=-5, fuel_pct=150, vin="x"))
        message = str(ctx.exception)
        self.assertIn("speed_kmh", message)
        self.assertIn("fuel_pct", message)
        self.assertIn("vin", message)

    def test_rejects_wrong_types(self):
        for bad in (True, "fast", float("nan"), None, [1]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_telemetry(reading(speed_kmh=bad))
        with self.assertRaises(ValueError):
            validate_telemetry("not an object")

    def test_missing_field_and_bad_timestamp(self):
        data = reading()
        del data["battery_v"]
        with self.assertRaises(ValueError):
            validate_telemetry(data)
        with self.assertRaises(ValueError):
            validate_telemetry(reading(timestamp="yesterday"))

    def test_tire_pressure_is_optional(self):
        data = reading()
        del data["tire_pressure_psi"]
        self.assertIsNone(validate_telemetry(data)["tire_pressure_psi"])


class RuleTests(unittest.TestCase):
    def types(self, **overrides):
        return {f["type"] for f in rules.evaluate(validate_telemetry(reading(**overrides)))}

    def test_healthy_vehicle_has_no_alerts(self):
        self.assertEqual(self.types(), set())

    def test_each_rule_fires(self):
        self.assertEqual(self.types(engine_temp_c=112), {"OVERHEAT"})
        self.assertEqual(self.types(fuel_pct=5), {"LOW_FUEL"})
        self.assertEqual(self.types(battery_v=11.2), {"LOW_BATTERY"})
        self.assertEqual(self.types(speed_kmh=140), {"SPEEDING"})
        self.assertEqual(self.types(tire_pressure_psi=20), {"LOW_TIRE"})

    def test_harsh_braking_needs_big_drop_in_short_time(self):
        prev = validate_telemetry(reading(speed_kmh=100, timestamp="2026-11-25T10:00:00+00:00"))
        quick = validate_telemetry(reading(speed_kmh=30, timestamp="2026-11-25T10:00:05+00:00"))
        slow = validate_telemetry(reading(speed_kmh=30, timestamp="2026-11-25T10:05:00+00:00"))
        small = validate_telemetry(reading(speed_kmh=80, timestamp="2026-11-25T10:00:05+00:00"))
        self.assertIn("HARSH_BRAKING", {f["type"] for f in rules.evaluate(quick, prev)})
        self.assertNotIn("HARSH_BRAKING", {f["type"] for f in rules.evaluate(slow, prev)})  # 5 minutes apart
        self.assertNotIn("HARSH_BRAKING", {f["type"] for f in rules.evaluate(small, prev)})  # only 20 km/h


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = CloudService(Store(":memory:"))

    def test_ingest_stores_and_updates_fleet(self):
        self.service.ingest(reading(speed_kmh=50))
        self.service.ingest(reading(speed_kmh=65, timestamp="2026-11-25T10:00:30+00:00"))
        fleet = self.service.fleet()
        self.assertEqual(len(fleet), 1)
        self.assertEqual(fleet[0]["speed_kmh"], 65)
        self.assertEqual(len(self.service.history("TEST-0001")), 2)

    def test_alert_opens_once_then_resolves(self):
        first = self.service.ingest(reading(engine_temp_c=115))
        again = self.service.ingest(reading(engine_temp_c=118, timestamp="2026-11-25T10:00:30+00:00"))
        self.assertEqual([a["type"] for a in first["new_alerts"]], ["OVERHEAT"])
        self.assertEqual(again["new_alerts"], [])  # same condition: no duplicate alert
        self.assertEqual(len(self.service.alerts(only_open=True)), 1)
        self.assertEqual(self.service.fleet()[0]["open_alerts"], 1)

        self.service.ingest(reading(engine_temp_c=90, timestamp="2026-11-25T10:01:00+00:00"))
        self.assertEqual(self.service.alerts(only_open=True), [])  # cooled down -> resolved
        self.assertEqual(len(self.service.alerts()), 1)  # but still in the history

    def test_harsh_braking_is_a_one_off_event(self):
        self.service.ingest(reading(speed_kmh=100))
        result = self.service.ingest(reading(speed_kmh=20, timestamp="2026-11-25T10:00:05+00:00"))
        self.assertEqual([a["type"] for a in result["new_alerts"]], ["HARSH_BRAKING"])
        self.assertEqual(self.service.alerts(only_open=True), [])  # events are never "open"

    def test_commands_are_delivered_exactly_once(self):
        self.service.ingest(reading())
        self.service.queue_command("test-0001", "flash_lights")
        first = self.service.ingest(reading(timestamp="2026-11-25T10:00:30+00:00"))
        second = self.service.ingest(reading(timestamp="2026-11-25T10:01:00+00:00"))
        self.assertEqual([c["command"] for c in first["commands"]], ["flash_lights"])
        self.assertEqual(second["commands"], [])

    def test_command_validation(self):
        with self.assertRaises(LookupError):
            self.service.queue_command("NEVER-SEEN", "flash_lights")
        self.service.ingest(reading())
        with self.assertRaises(ValueError):
            self.service.queue_command("TEST-0001", "self_destruct")
        with self.assertRaises(ValueError):
            self.service.queue_command("bad vin!", "flash_lights")

    def test_vehicles_are_kept_separate(self):
        self.service.ingest(reading(vin="CAR-AAAA", engine_temp_c=120))
        self.service.ingest(reading(vin="CAR-BBBB"))
        self.assertEqual([a["vin"] for a in self.service.alerts(only_open=True)], ["CAR-AAAA"])
        self.assertEqual(self.service.alerts(vin="car-bbbb"), [])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(CloudService(Store(":memory:")), KEY, port=0)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, body=None, key=KEY, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-API-Key"] = key
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def test_health_and_dashboard_need_no_key(self):
        self.assertEqual(self.call("GET", "/health", key=None)[0], 200)
        status, body = self.call("GET", "/", key=None)
        self.assertEqual(status, 200)
        self.assertIn(b"Fleet Dashboard", body)

    def test_api_requires_the_key(self):
        self.assertEqual(self.call("GET", "/api/vehicles", key=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/vehicles", key="wrong")[0], 401)
        self.assertEqual(self.call("POST", "/api/telemetry", reading(), key="wrong")[0], 401)

    def test_ingest_then_query(self):
        status, body = self.call("POST", "/api/telemetry", reading(vin="HTTP-0001", engine_temp_c=120))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["new_alerts"][0]["type"], "OVERHEAT")
        fleet = json.loads(self.call("GET", "/api/vehicles")[1])
        self.assertIn("HTTP-0001", [v["vin"] for v in fleet])
        history = json.loads(self.call("GET", "/api/vehicles/HTTP-0001/telemetry?limit=5")[1])
        self.assertEqual(len(history), 1)
        alerts = json.loads(self.call("GET", "/api/alerts?open=1&vin=HTTP-0001")[1])
        self.assertEqual(alerts[0]["type"], "OVERHEAT")

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/api/telemetry", raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/telemetry", reading(speed_kmh=-1))[0], 400)
        self.assertEqual(self.call("POST", "/api/telemetry", raw=b"x" * (70 * 1024))[0], 413)
        self.assertEqual(self.call("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.call("GET", "/api/vehicles/bad%20vin/telemetry")[0], 404)

    def test_remote_command_flow(self):
        self.call("POST", "/api/telemetry", reading(vin="HTTP-0002"))
        status, _ = self.call("POST", "/api/vehicles/HTTP-0002/commands", {"command": "lock_doors"})
        self.assertEqual(status, 202)
        self.assertEqual(self.call("POST", "/api/vehicles/HTTP-0002/commands", {"command": "bogus"})[0], 400)
        self.assertEqual(self.call("POST", "/api/vehicles/NOT-KNOWN/commands", {"command": "lock_doors"})[0], 404)
        _, body = self.call("POST", "/api/telemetry", reading(vin="HTTP-0002", timestamp="2026-11-25T10:00:30+00:00"))
        self.assertEqual(json.loads(body)["commands"][0]["command"], "lock_doors")


class ClientBufferingTests(unittest.TestCase):
    def test_buffers_while_offline_then_delivers_in_order(self):
        service = CloudService(Store(":memory:"))
        server = make_server(service, KEY, port=0)
        live_url = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        client = CloudClient("http://127.0.0.1:1", KEY, timeout=0.5)  # nothing listens on port 1
        client.send(reading(vin="BUF-0001", speed_kmh=10, timestamp="2026-11-25T10:00:00+00:00"))
        client.send(reading(vin="BUF-0001", speed_kmh=20, timestamp="2026-11-25T10:00:10+00:00"))
        self.assertEqual(len(client.buffer), 2)
        self.assertEqual(service.fleet(), [])

        client.base_url = live_url  # connection restored
        client.flush()
        self.assertEqual(len(client.buffer), 0)
        speeds = [r["speed_kmh"] for r in reversed(service.history("BUF-0001"))]
        self.assertEqual(speeds, [10, 20])  # arrived in the original order

    def test_unacceptable_reading_is_dropped_not_retried_forever(self):
        server = make_server(CloudService(Store(":memory:")), KEY, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = CloudClient(f"http://127.0.0.1:{server.server_address[1]}", KEY)
        client.send(reading(speed_kmh=-50))  # invalid -> HTTP 400 -> dropped
        self.assertEqual(len(client.buffer), 0)

    def test_buffer_has_a_size_limit(self):
        client = CloudClient("http://127.0.0.1:1", KEY, timeout=0.2, buffer_limit=3)
        for i in range(5):
            client.send(reading(speed_kmh=i))
        self.assertEqual([r["speed_kmh"] for r in client.buffer], [2, 3, 4])  # oldest dropped


if __name__ == "__main__":
    unittest.main()
