"""Atomic admission and deliberate-stop control shared by all product workers."""

import os
import secrets
import threading
import uuid
from contextlib import contextmanager

from dlubal_mcp import __version__


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

    def reconcile(self):
        """Release records whose uncertainty has ended, before control answers.

        Outside self._lock: reconciliation takes the journal's worker lock and
        its own database transaction. Journal maintenance never fails control.
        """
        with self._lock:
            managers = list(self._managers)
        for manager in managers:
            manager.reconcile_orphans()

    def prepare_stop(self, allow_unresolved=False):
        """Drain for a deliberate stop.

        By default any unfinished record prevents draining, which is what
        Connectors before 2.30.17 expect: they treat a lease that comes back
        with operations as a refusal and never resume it. A caller that opts in
        with allow_unresolved may stop THIS process while unresolved records
        remain: the records are durable, survive the restart and keep blocking
        new work. Live work of this process, active readers and a worker lock
        held by any process still refuse.
        """
        with self._lock:
            result = self.snapshot()
            if allow_unresolved:
                # An 'unknown' row may be another process's LIVE work, which this
                # process cannot see; the journal's worker lock is the proof.
                ready = (
                    not result["active_reads"]
                    and not any(
                        item["state"] == "running" for item in result["operations"]
                    )
                    and (
                        not result["operations"]
                        or all(manager.no_live_worker() for manager in self._managers)
                    )
                )
            else:
                ready = result["state"] in {"idle", "draining"}
            if not ready:
                return result
            # No expiry: a suspended connector must never kill a worker admitted
            # after a lease timeout. Failure requires explicit resume or retry.
            self._lease = self._lease or secrets.token_hex(32)
            return {**result, "state": "draining", "lease": self._lease}

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
