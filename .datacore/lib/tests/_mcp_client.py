"""Minimal MCP stdio client for promise evals (newline-delimited JSON-RPC).

Used by KNW-1 (capture routing) and MOD-7 (tools start and answer in time).
Every read has a deadline, so a hung server fails the eval instead of hanging it.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import time


class McpTimeout(AssertionError):
    pass


class McpClient:
    def __init__(self, argv, env=None, cwd=None):
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env, cwd=cwd)
        self._buf = b""
        self._id = 0

    def _send(self, msg):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def _read_line(self, deadline):
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise McpTimeout("no answer before the deadline")
            ready, _, _ = select.select([self.proc.stdout], [], [], left)
            if not ready:
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise AssertionError("server closed stdout; stderr: " + self.stderr_tail())
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def request(self, method, params=None, timeout=30.0):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            line = self._read_line(deadline).strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == rid:
                return msg

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self, timeout=30.0):
        r = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "promise-eval", "version": "1"}}, timeout)
        self.notify("notifications/initialized")
        return r

    def call(self, name, arguments, timeout=30.0):
        return self.request("tools/call", {"name": name, "arguments": arguments}, timeout)

    def stderr_tail(self):
        try:
            self.proc.kill()
            return (self.proc.communicate(timeout=5)[1] or b"").decode(errors="replace")[-1500:]
        except Exception:  # noqa: BLE001
            return ""

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.proc.kill()
