"""The vehicle's connection to the cloud.

Cars lose signal in tunnels and parking garages, so readings are BUFFERED
and sent later, in order, once the connection is back.
"""

import json
import logging
import urllib.error
import urllib.request
from collections import deque

log = logging.getLogger("cloud_client")


class CloudClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 3.0, buffer_limit: int = 1000):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # If the car is offline for very long, the OLDEST readings are dropped first
        self.buffer: deque = deque(maxlen=buffer_limit)

    def api(self, method: str, path: str, body=None) -> tuple[int, dict]:
        """One HTTP call. Returns (status, json). Raises OSError if unreachable."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "X-API-Key": self.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as err:  # server answered with an error code
            try:
                return err.code, json.loads(err.read() or b"{}")
            except ValueError:
                return err.code, {}

    def send(self, reading: dict) -> list[dict]:
        """Queue a reading and try to deliver everything buffered.

        Returns any remote commands the cloud sent back.
        """
        self.buffer.append(reading)
        return self.flush()

    def flush(self) -> list[dict]:
        commands: list[dict] = []
        while self.buffer:
            try:
                status, body = self.api("POST", "/api/telemetry", self.buffer[0])
            except OSError as err:  # no connection: keep the data, try again later
                log.warning("Cloud unreachable (%s) - %d reading(s) buffered", err, len(self.buffer))
                break
            if status == 200:
                self.buffer.popleft()
                commands.extend(body.get("commands", []))
            elif status in (400, 413):  # the cloud will never accept this reading: drop it
                log.error("Cloud rejected a reading (%s): %s", status, body.get("error"))
                self.buffer.popleft()
            else:  # 401 / 5xx: keep the data, try again later
                log.warning("Cloud returned %s - %d reading(s) buffered", status, len(self.buffer))
                break
        return commands
