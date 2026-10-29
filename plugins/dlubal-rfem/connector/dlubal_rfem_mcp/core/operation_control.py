"""Atomic admission and deliberate-stop control shared by all product workers."""

import os
import secrets
import threading
import uuid
from contextlib import contextmanager

from dlubal_rfem_mcp import __version__


class OperationControl:
    def __init__(self):
        self._lock = threading.RLock()
        self._managers = []
        self._readers = 0
        self._lease = ""
        self.instance_id = str(uuid.uuid4())

    def register(self, manager):
        with self._lock:
            self._managers.append(manager)

    @contextmanager
    def admission(self):
        # Hold only across durable admission, never the operation's HTTP wait.
        with self._lock:
            if self._lease:
                raise RuntimeError("MCP is draining for a deliberate restart")
            yield

    @contextmanager
    def reading(self):
        with self.admission():
            self._readers += 1
        try:
            yield
        finally:
            with self._lock:
                self._readers -= 1

    def snapshot(self):
        with self._lock:
            operations = [
                operation
                for manager in self._managers
                for operation in manager.unfinished()
            ]
            state = (
                "unknown"
                if any(item["state"] == "unknown" for item in operations)
                else "running"
                if operations or self._readers
                else "draining"
                if self._lease
                else "idle"
            )
            return {
                "protocol": 1,
                "version": __version__,
                "pid": os.getpid(),
                "instance_id": self.instance_id,
                "state": state,
                "active_reads": self._readers,
                "operations": operations,
            }

    def prepare_stop(self):
        with self._lock:
            result = self.snapshot()
            if result["state"] not in {"idle", "draining"}:
                return result
            # No expiry: a suspended connector must never kill a worker admitted
            # after a lease timeout. Failure requires explicit resume or retry.
            self._lease = self._lease or secrets.token_hex(32)
            return {**result, "state": "draining", "lease": self._lease}

    def execution_status(self, binding, sequence):
        from dlubal_rfem_mcp.core.config import ConnectorExecutionStatusV1

        try:
            snapshot = self.snapshot()
            state = snapshot["state"]
            in_flight = snapshot["active_reads"] + len(snapshot["operations"])
        except Exception:  # noqa: BLE001 - an unreadable execution journal cannot prove idle
            state, in_flight = "unverifiable", 0
        return ConnectorExecutionStatusV1(
            connectorInstanceId=binding["connectorInstanceId"],
            generation=binding["generation"],
            sequence=sequence,
            state=state,
            inFlight=in_flight,
        )

    def resume(self, lease):
        with self._lock:
            if (
                not isinstance(lease, str)
                or not self._lease
                or not secrets.compare_digest(lease, self._lease)
            ):
                raise ValueError("Invalid restart lease")
            self._lease = ""
            return self.snapshot()
