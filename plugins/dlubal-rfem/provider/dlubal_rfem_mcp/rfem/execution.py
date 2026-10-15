"""Durable admission and observable lifetime of RFEM script workers.

The HTTP wait is not the operation lifetime. Unfinished records from a previous
process block new work: RFEM has no supported API to prove cancellation.
"""

import builtins
import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

logger = logging.getLogger("dlubal_rfem_mcp.rfem")

MAX_OUTPUT = 64 * 1024


class ExecutionUncertain(RuntimeError):
    """The connection could not be closed; subsequent work must stay blocked."""


@contextmanager
def worker_lock(path):
    """OS releases this lock on process death; recovery cannot unlock live work."""
    with open(str(path) + ".worker-lock", "a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class Capture:
    def __init__(self):
        self._lock = threading.Lock()
        self._text = ""
        self._truncated = False

    def write(self, text):
        with self._lock:
            remaining = MAX_OUTPUT - len(self._text)
            self._text += text[:remaining]
            self._truncated |= len(text) > remaining
        return len(text)

    def flush(self):
        pass

    def print(self, *args, sep=" ", end="\n", file=None, flush=False):
        if file is not None:
            raise ValueError("Script print output cannot be redirected")
        builtins.print(*args, sep=sep, end=end, file=self, flush=flush)

    def text(self):
        with self._lock:
            return self._text + ("\n[Output truncated]" if self._truncated else "")


class ExecutionManager:
    def __init__(
        self, path: Path, endpoint: str = "127.0.0.1:9000", control=None, product="rfem"
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.endpoint = endpoint
        self.path = path
        self.control = control
        self.product = product
        self.owner = str(uuid.uuid4())
        self._lock = threading.Lock()
        self._workers: dict[str, tuple[threading.Event, Capture]] = {}
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, owner TEXT NOT NULL,
                context TEXT NOT NULL, state TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', model_guid TEXT, endpoint TEXT NOT NULL, output_available INTEGER NOT NULL DEFAULT 1, started REAL NOT NULL, finished REAL)""")
            db.execute(
                "CREATE TABLE IF NOT EXISTS bindings (context TEXT PRIMARY KEY, guid TEXT NOT NULL, endpoint TEXT NOT NULL)"
            )
        if control is not None:
            control.register(self)

    def unfinished(self):
        # Admission takes control -> manager -> database locks in this order.
        # A durable running row without a live worker is uncertainty, including
        # failed thread creation and a worker whose terminal journal write failed.
        with self._lock, self._db() as db:
            rows = db.execute(
                "SELECT id, owner, state, started FROM executions WHERE state IN ('running', 'unknown') ORDER BY started"
            ).fetchall()
            return [
                {
                    "execution_id": row["id"],
                    "product": self.product,
                    "state": "unknown"
                    if row["owner"] != self.owner
                    or (
                        row["state"] == "running"
                        and not self._has_live_worker(row["id"])
                    )
                    else row["state"],
                    "started": row["started"],
                }
                for row in rows
            ]

    def _has_live_worker(self, execution_id):
        # Caller holds self._lock; a set event also covers the short interval
        # between failed completion persistence and removal from the worker map.
        worker = self._workers.get(execution_id)
        return worker is not None and not worker[0].is_set()

    def _db(self):
        # Each caller closes its connection (sqlite's context manager only commits).
        from contextlib import contextmanager

        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

        return connection()

    def binding(self, context):
        if not context:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT guid, endpoint FROM bindings WHERE context=?", (context,)
            ).fetchone()
            if row and row["endpoint"] != self.endpoint:
                raise RuntimeError(
                    "This chat is bound to another RFEM endpoint. Select the intended model explicitly; no automatic instance switch is allowed."
                )
            return row[0] if row else None

    def bind(self, context, guid):
        if context:
            with self._db() as db:
                db.execute(
                    "INSERT OR REPLACE INTO bindings VALUES (?, ?, ?)",
                    (context, guid, self.endpoint),
                )

    def record_model(self, execution_id, context, guid):
        with self._db() as db:
            db.execute(
                "UPDATE executions SET model_guid=? WHERE id=?", (guid, execution_id)
            )
            if context:
                db.execute(
                    "INSERT OR REPLACE INTO bindings VALUES (?, ?, ?)",
                    (context, guid, self.endpoint),
                )

    def status(self, execution_id):
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM executions WHERE id=?", (execution_id,)
            ).fetchone()
        if row is None:
            return {"execution_id": execution_id, "state": "not_found"}
        result = {
            key: row[key]
            for key in (
                "state",
                "output",
                "error",
                "started",
                "finished",
                "endpoint",
                "output_available",
            )
        }
        result["execution_id"] = execution_id
        result["model_guid"] = row["model_guid"]
        if row["state"] == "running":
            with self._lock:
                worker = self._workers.get(execution_id)
                if (
                    worker is not None
                    and row["owner"] == self.owner
                    and self._has_live_worker(execution_id)
                ):
                    result["output"] = worker[1].text()
                else:
                    result["state"] = "unknown"
                    result["error"] = (
                        "Execution owner is unavailable. Do not replay. Operator verification of RFEM is required."
                    )
        return result

    def submit(self, execution_id, context, fingerprint, work, wait_seconds=120):
        execution_id = (
            str(uuid.UUID(execution_id)) if execution_id else str(uuid.uuid4())
        )
        if len(context) > 128:
            raise ValueError("context_id exceeds 128 characters")
        capture = Capture()
        finished = threading.Event()
        with self.control.admission() if self.control else nullcontext(), self._lock:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT fingerprint, context FROM executions WHERE id=?",
                    (execution_id,),
                ).fetchone()
                if existing:
                    if (
                        existing["fingerprint"] != fingerprint
                        or existing["context"] != context
                    ):
                        return {
                            "execution_id": execution_id,
                            "state": "rejected",
                            "error": "Execution ID was already used for different input.",
                        }
                else:
                    active = db.execute(
                        "SELECT id FROM executions WHERE state IN ('running', 'unknown') LIMIT 1"
                    ).fetchone()
                    if active:
                        return {
                            "execution_id": execution_id,
                            "state": "busy",
                            "active_execution_id": active[0],
                            "error": "RFEM has an unfinished execution. This script was not submitted.",
                        }
                    db.execute(
                        "INSERT INTO executions (id, fingerprint, owner, context, state, started, endpoint) VALUES (?, ?, ?, ?, 'running', ?, ?)",
                        (
                            execution_id,
                            fingerprint,
                            self.owner,
                            context,
                            time.time(),
                            self.endpoint,
                        ),
                    )
            if not existing:
                self._workers[execution_id] = (finished, capture)

                def run():
                    state, error = "succeeded", ""
                    try:
                        with worker_lock(self.path):
                            # Recovery may have resolved admission before this
                            # thread acquired its OS lock. Never execute then.
                            with self._db() as db:
                                admitted = db.execute(
                                    "SELECT state FROM executions WHERE id=?",
                                    (execution_id,),
                                ).fetchone()[0]
                            if admitted != "running":
                                raise RuntimeError(
                                    "Execution was resolved before its worker started; script not submitted."
                                )
                            work(
                                capture,
                                lambda guid: self.record_model(
                                    execution_id, context, guid
                                ),
                            )
                    except ExecutionUncertain as exc:
                        state, error = "unknown", str(exc)[:8192]
                    except Exception as exc:
                        logger.debug("RFEM script failed", exc_info=True)
                        # A gRPC deadline/disconnect can outlive the Python call
                        # on RFEM's side. A closed channel is not cancellation.
                        code = getattr(exc, "code", None)
                        try:
                            rpc_status = (
                                str(code()).split(".")[-1] if callable(code) else None
                            )
                            uncertain = rpc_status in {
                                "DEADLINE_EXCEEDED",
                                "CANCELLED",
                                "UNAVAILABLE",
                            }
                        except Exception:
                            logger.debug(
                                "RFEM exception status is unavailable", exc_info=True
                            )
                            uncertain = True
                            rpc_status = "UNAVAILABLE"
                        state = "unknown" if uncertain else "failed"
                        if rpc_status is not None:
                            error = (
                                "RFEM transport ended before completion could be confirmed. "
                                "Inspect RFEM before retrying."
                                if uncertain
                                else "RFEM rejected the operation. Inspect RFEM validation messages."
                            )
                        else:
                            error = str(exc)[:8192]
                    except BaseException:
                        logger.debug("RFEM worker interrupted", exc_info=True)
                        state, error = (
                            "unknown",
                            "Execution worker was interrupted; inspect RFEM before further work.",
                        )
                    finally:
                        try:
                            with self._db() as db:
                                db.execute(
                                    "UPDATE executions SET state=?, output=?, error=?, finished=? WHERE id=?",
                                    (
                                        state,
                                        capture.text(),
                                        error,
                                        time.time(),
                                        execution_id,
                                    ),
                                )
                                # Retain idempotency tombstones indefinitely; bound saved
                                # output to the latest 256 completed operations.
                                db.execute(
                                    "UPDATE executions SET output='', output_available=0 WHERE id IN (SELECT id FROM executions WHERE state IN ('succeeded', 'failed') ORDER BY finished DESC LIMIT -1 OFFSET 256)"
                                )
                        finally:
                            finished.set()
                            with self._lock:
                                self._workers.pop(execution_id, None)

                try:
                    thread = threading.Thread(
                        target=run, daemon=True, name="rfem-execution"
                    )
                    thread.start()
                except Exception:
                    logger.debug("Execution worker could not start", exc_info=True)
                    # Admission is already durable. Return status with the same
                    # ID instead of losing it in a generic tool rejection. Keep
                    # the journal tombstone so no caller can replay this input.
                    self._workers.pop(execution_id, None)
                    finished.set()
        if not existing:
            finished.wait(wait_seconds)
        return self.status(execution_id)


def default_state_path():
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "state")))
    # Shared across endpoints: moving to another port must not forget a chat's
    # instance binding or bypass an unfinished operation on the old endpoint.
    return root / "DlubalMCP" / "rfem-executions.sqlite3"


def fingerprint(code, model_guid):
    return hashlib.sha256(
        json.dumps([code, model_guid], separators=(",", ":")).encode()
    ).hexdigest()


def result_text(result):
    state = result["state"]
    marker = (
        "Output:"
        if state == "succeeded"
        else "TimeoutError:"
        if state == "running"
        else "RuntimeError:"
    )
    summary = {key: value for key, value in result.items() if key != "output"}
    return f"{marker} RFEM execution {json.dumps(summary)}\n{result.get('output', '')}"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Resolve an orphaned RFEM execution only after an operator verified RFEM is idle and inspected partial model changes. Never reruns a script."
    )
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--resolve", required=True)
    parser.add_argument("--confirmed-rfem-idle", action="store_true", required=True)
    args = parser.parse_args()
    manager = ExecutionManager(args.state_file)
    with worker_lock(args.state_file), manager._db() as db:
        db.execute(
            "UPDATE executions SET state='failed', error='Operator resolved unknown execution; inspect partial changes before further work.', finished=? WHERE id=? AND state IN ('running', 'unknown')",
            (time.time(), str(uuid.UUID(args.resolve))),
        )
    print(json.dumps(manager.status(str(uuid.UUID(args.resolve)))))
