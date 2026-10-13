"""Local OpenAI-compatible model client with bounded structured decisions."""
from __future__ import annotations
import ipaddress
import http.client
import json
import socket
import threading
import time
import urllib.parse


class ModelError(RuntimeError):
    pass


def bounded_decision(value):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > 12 or nodes > 512:
            raise ModelError("Model decision nesting or item limit exceeded")
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    if len(json.dumps(value, allow_nan=False).encode()) > 32768:
        raise ModelError("Model decision exceeds size limit")
    return value


class LocalModel:
    def __init__(self, base_url, model="", timeout_seconds=120, max_tokens=1024):
        parsed = urllib.parse.urlsplit(base_url)
        host = parsed.hostname or ""
        local = host == "localhost"
        try:
            local = local or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parsed.scheme != "http" or not local or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Model URL must be an HTTP loopback endpoint without credentials")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = min(120, max(1, float(timeout_seconds)))
        self.max_tokens = min(4096, max(128, int(max_tokens)))

    def _request(self, path, value=None, *, timeout_seconds=None):
        data = None if value is None else json.dumps(value, allow_nan=False).encode()
        endpoint = urllib.parse.urlsplit(self.base_url + path)
        connection = watchdog = None
        expired = threading.Event()
        try:
            timeout = self.timeout_seconds if timeout_seconds is None else min(self.timeout_seconds, max(.1, timeout_seconds))
            deadline = time.monotonic() + timeout
            connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=timeout)
            connection.connect()
            connected_socket = connection.sock
            def interrupt_request():
                expired.set()
                try:
                    connected_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelError("Model request time budget reached")
            watchdog = threading.Timer(remaining, interrupt_request)
            watchdog.daemon = True
            watchdog.start()
            # HTTPConnection uses neither proxy environment variables nor redirects.
            connection.request("GET" if data is None else "POST", endpoint.path or "/", body=data,
                               headers={"Content-Type": "application/json"})
            with connection.getresponse() as response:
                if 300 <= response.status < 400:
                    raise ModelError("Model endpoint redirects are disabled")
                if not 200 <= response.status < 300:
                    raise ModelError(f"Local model returned HTTP {response.status}")
                chunks, total = [], 0
                while True:
                    if response.isclosed():
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ModelError("Model response time budget reached")
                    # The timer also interrupts incomplete/dripping response headers.
                    connected_socket.settimeout(max(.001, remaining))
                    chunk = response.read1(min(65536, 2 * 1024 * 1024 + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > 2 * 1024 * 1024:
                        raise ModelError("Model response exceeds limit")
                body = b"".join(chunks)
            if expired.is_set() or time.monotonic() >= deadline:
                raise ModelError("Model response time budget reached")
            if len(body) > 2 * 1024 * 1024:
                raise ModelError("Model response exceeds limit")
            return json.loads(body)
        except (http.client.HTTPException, ValueError, OSError) as exc:
            if expired.is_set():
                raise ModelError("Model response time budget reached") from exc
            raise ModelError(f"Local model request failed: {exc}") from exc
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if connection is not None:
                connection.close()

    def discover(self, *, timeout_seconds=None):
        models = self._request("/models", timeout_seconds=timeout_seconds).get("data", [])
        if not models or not isinstance(models[0].get("id"), str):
            raise ModelError("No model is served at the configured endpoint")
        self.model = models[0]["id"]
        return self.model

    def decide(self, messages, *, timeout_seconds=None):
        deadline = time.monotonic() + (self.timeout_seconds if timeout_seconds is None else timeout_seconds)
        if not self.model:
            self.discover(timeout_seconds=deadline - time.monotonic())
        if time.monotonic() >= deadline:
            raise ModelError("Model decision time budget reached")
        result = self._request("/chat/completions", {
            "model": self.model, "messages": messages, "temperature": 0.3,
            "max_tokens": self.max_tokens, "stream": False}, timeout_seconds=deadline - time.monotonic())
        try:
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Expected text response")
            value = content.strip()
            if value.startswith("```json") and value.endswith("```"):
                value = value[7:-3].strip()
            elif value.startswith("```") and value.endswith("```"):
                value = value[3:-3].strip()
            decision = json.loads(value)
            if not isinstance(decision, dict) or not isinstance(decision.get("tool"), str) or not isinstance(decision.get("arguments", {}), dict):
                raise ValueError("Expected tool and arguments")
            bounded_decision(decision)
            return decision, content
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError(f"Invalid structured model decision: {exc}") from exc
