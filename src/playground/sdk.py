"""Small standard-library client for the mediated robot gateway."""
from __future__ import annotations
import json
import urllib.error
import urllib.request


class RobotClient:
    """Worker client. Arming is reserved for a human operator outside the SDK."""
    def __init__(self, base_url: str, token: str, lease_id: str | None = None, timeout=2.0):
        self.base_url = base_url.rstrip("/")
        self.token, self.lease_id, self.timeout = token, lease_id, timeout

    def _request(self, path, payload=None):
        request = urllib.request.Request(self.base_url + "/robot/" + path,
                                        data=None if payload is None else json.dumps(payload, allow_nan=False).encode(),
                                        headers={"Authorization": "Bearer " + self.token,
                                                 "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode(errors="replace")
            raise RuntimeError(f"robot request failed ({exc.code}): {detail}") from exc

    def acquire(self, owner: str, ttl_s=10.0):
        lease = self._request("lease", {"owner": owner, "ttl_s": ttl_s})
        self.lease_id = lease["lease_id"]
        return lease

    def observe(self):
        return self._request("observe")

    def action(self, left: float, right: float, duration_s: float = 0.2, gripper=None):
        return self._request("action", {"lease_id": self.lease_id, "left": left, "right": right,
                                        "duration_s": duration_s, "gripper": gripper})

    def heartbeat(self):
        return self._request("heartbeat", {"lease_id": self.lease_id})

    def stop(self):
        return self._request("stop", {})

    def release(self):
        result = self._request("release", {"lease_id": self.lease_id})
        self.lease_id = None
        return result
