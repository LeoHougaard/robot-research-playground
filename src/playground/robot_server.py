"""Optional standalone loopback gateway for simulation and bench control."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets

from .robot import RobotController, RobotError
from .simulation import SimulationBackend


def make_handler(controller: RobotController, worker_token: str, operator_token: str):
    if not worker_token or not operator_token or worker_token == operator_token:
        raise ValueError("separate nonempty worker and operator tokens required")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _reply(self, code, data):
            body = json.dumps(data, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _role(self):
            auth = self.headers.get("Authorization", "")
            if secrets.compare_digest(auth, "Bearer " + operator_token):
                return "operator"
            if secrets.compare_digest(auth, "Bearer " + worker_token):
                return "worker"
            return None

        def do_GET(self):
            if not self._role():
                return self._reply(401, {"error": "authentication required"})
            if self.path != "/robot/observe":
                return self._reply(404, {"error": "unknown route"})
            try:
                self._reply(200, controller.observe())
            except Exception:
                controller.stop("observation failed")
                self._reply(503, {"error": "robot observation unavailable"})

        def do_POST(self):
            role = self._role()
            if role is None:
                return self._reply(401, {"error": "authentication required"})
            if self.path in ("/robot/arm", "/robot/renew") and role != "operator":
                return self._reply(403, {"error": "operator authorization required"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise RobotError("invalid request size")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise RobotError("request must be a JSON object")
                lease_id = data.get("lease_id")
                if self.path == "/robot/lease":
                    result = controller.acquire(data["owner"], data.get("ttl_s", 10.0))
                elif self.path == "/robot/arm":
                    result = controller.arm(lease_id, human_confirmed=data.get("human_confirmed") is True)
                elif self.path == "/robot/renew":
                    result = controller.renew(lease_id, data.get("ttl_s", 10.0))
                elif self.path == "/robot/action":
                    result = controller.action(lease_id, data["left"], data["right"], data["duration_s"], data.get("gripper"))
                elif self.path == "/robot/heartbeat":
                    result = controller.heartbeat(lease_id)
                elif self.path == "/robot/release":
                    controller.release(lease_id)
                    result = {"released": True}
                elif self.path == "/robot/stop":
                    controller.stop()
                    result = {"stopped": True}
                else:
                    return self._reply(404, {"error": "unknown route"})
                self._reply(200, result)
            except (RobotError, ValueError, TypeError, KeyError) as exc:
                self._reply(400, {"error": str(exc)})
            except Exception:
                controller.stop("gateway backend error")
                self._reply(503, {"error": "robot backend unavailable"})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    worker_token, operator_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    controller = RobotController(SimulationBackend())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(controller, worker_token, operator_token))
    print(f"Simulation gateway http://127.0.0.1:{args.port}")
    print("Worker token:", worker_token)
    print("Operator token:", operator_token)
    print("Starts disarmed. Acquire lease, then POST /robot/arm with operator token and human_confirmed=true.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.server_close()


if __name__ == "__main__":
    main()
