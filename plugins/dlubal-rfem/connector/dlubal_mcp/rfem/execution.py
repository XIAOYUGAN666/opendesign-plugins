"""Durable admission and observable lifetime of RFEM script workers.

The HTTP wait is not the operation lifetime. Unfinished records from a previous
process block new work: RFEM has no supported API to prove cancellation.
"""

import builtins
import contextvars
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from dlubal_mcp.rfem import stale_release
from dlubal_mcp.rfem.stale_release import worker_lock

logger = logging.getLogger("dlubal_mcp.rfem")

MAX_OUTPUT = 64 * 1024


class _Recorder:
    """What a worker is handed to write back into the journal.

    Callable as record(guid) exactly as before, so every work() implementation
    and every test that passes a plain function keeps working. The extra
    .mutated() is how BoundApplication persists that a model write was
    attempted â€” the single fact reconcile_orphans needs to tell an abandoned
    execution that changed nothing from one that may have changed a model.
    """

    def __init__(self, manager, execution_id, context):
        self._manager = manager
        self._execution_id = execution_id
        self._context = context
        self.mutation_attempted = False

    def __call__(self, guid):
        self._manager.record_model(self._execution_id, self._context, guid)

    def mutated(self):
        self.mutation_attempted = True
        self._manager.mark_mutated(self._execution_id)


def _blocked_reason(active):
    """Why a submission was refused, in terms the operator can act on.

    Reconciliation has already run by this point, so a 'running' row is either
    genuinely live or inside its grace window; only 'unknown' means a task ended
    without RFEM being verified.
    """
    if active["state"] == "unknown":
        return (
            "A previous task ended unexpectedly after it had begun changing the model, "
            "and RFEM has not been verified since. This script was not submitted. "
            "Inspect the model in RFEM for partial changes, save what you keep, then "
            f"close RFEM: execution {active['id']} is released automatically once the "
            "RFEM session that ran it has ended. Reinstalling RFEM or this server does "
            "not clear it: the record lives in the execution journal."
        )
    return (
        "RFEM is still executing a previous task. This script was not submitted. "
        f"Wait for execution {active['id']} to finish, then retry."
    )


# RFEM serves one API client at a time and finishes a request even after its
# client is gone (no cancellation on RFEM's side). A script stopped at its time
# limit therefore leaves RFEM busy for a while, and the next script is refused
# with this text. The raw gRPC error reads like a broken installation.
RFEM_BUSY_MARKER = "another client is already connected"
RFEM_BUSY_ERROR = (
    "RFEM is still busy with a request from another client, typically a previous "
    "script that was stopped at its time limit while RFEM kept working on it. "
    "This execution did not attempt any model writes. Wait a minute or two and run the script again; "
    "restarting RFEM is not needed."
)

RFEM_BUSY_UNSAFE_RETRY_ERROR = (
    "RFEM is busy with a request from another client. "
    "A model write was already attempted or the execution outcome is uncertain. "
    "Earlier changes may remain. Do not replay the script. "
    "Inspect the model for partial changes before requesting further work."
)


class ExecutionUncertain(RuntimeError):
    """The connection could not be closed; subsequent work must stay blocked."""


UNCLASSIFIED_FAILURE = (
    "Execution failure details are unavailable. Do not replay the script. "
    "Inspect the model for partial changes before further work."
)


def _failure_details(exc, mutation_attempted):
    """Diagnostic access must never turn an exception into a successful result."""
    try:
        if isinstance(exc, ExecutionUncertain):
            return "unknown", str(exc)[:8192]
        # A gRPC deadline/disconnect can outlive the Python call on RFEM's side.
        # The status property itself, its call, and its text may all fail.
        code = getattr(exc, "code", None)
        uncertain = callable(code) and str(code()).split(".")[-1] in {
            "DEADLINE_EXCEEDED",
            "CANCELLED",
            "UNAVAILABLE",
        }
        error = str(exc)
        if RFEM_BUSY_MARKER in error.lower():
            guidance = (
                RFEM_BUSY_UNSAFE_RETRY_ERROR
                if mutation_attempted or uncertain
                else RFEM_BUSY_ERROR
            )
            error = f"{guidance} RFEM said: {error}"
        return ("unknown" if uncertain else "failed"), error[:8192]
    except Exception:
        logger.debug("Execution failure details are unavailable", exc_info=True)
        return "unknown", UNCLASSIFIED_FAILURE


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
        self._workers = {}
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, owner TEXT NOT NULL,
                context TEXT NOT NULL, state TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', model_guid TEXT, endpoint TEXT NOT NULL, output_available INTEGER NOT NULL DEFAULT 1, started REAL NOT NULL, finished REAL)""")
            db.execute(
                "CREATE TABLE IF NOT EXISTS bindings (context TEXT PRIMARY KEY, guid TEXT NOT NULL, endpoint TEXT NOT NULL)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(executions)")}
            if "mutated" not in columns:
                db.execute(
                    "ALTER TABLE executions ADD COLUMN mutated INTEGER NOT NULL DEFAULT 0"
                )
                # Journals written before 1.0.15 carry no mutation record, so an
                # unfinished row from that era cannot be proven harmless. Treat
                # it as uncertain rather than silently clearing it.
                db.execute(
                    "UPDATE executions SET mutated=1 WHERE state IN ('running', 'unknown')"
                )
        self.reconcile_orphans()
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

    def mark_mutated(self, execution_id):
        """Persist that this execution attempted to change the model."""
        with self._db() as db:
            db.execute("UPDATE executions SET mutated=1 WHERE id=?", (execution_id,))

    def reconcile_orphans(self, grace_seconds=30):
        """Close out executions whose owner process died. Returns (cleared, held).

        A 'running' row is only ever closed by the process that opened it. When
        that process dies â€” a killed stdio session, a crash, a closed terminal â€”
        nobody writes the ending. The row stays 'running' forever and admission
        then refuses EVERY later execution from EVERY instance, because the
        journal is shared across them. Observed three times while testing stdio
        on 2026-09-15; each time it also blocked CALC behind a green status.

        Holding worker_lock is the proof that no worker is alive anywhere: a
        live worker holds it for the whole of work(), and Windows releases it on
        process death. If it cannot be taken, real work is in progress and
        nothing here is orphaned.

        An orphan that never attempted a model write is resolved to 'failed' and
        stops blocking â€” it cannot have left RFEM half-changed. One that did is
        moved to 'unknown' and KEEPS blocking while the RFEM session that ran it
        is still open: a partially applied change may sit in its memory and must
        be seen by a person. Once that session has ended, the record is released
        (stale_release.py explains the proof). The --resolve operator command in
        this module remains for the rest. Never reruns anything.

        Also called from the control endpoint, so a record releases itself once
        the session ended even when no new work is submitted (a Connector update).
        """
        try:
            counts = stale_release.release(self.path, self.product, grace_seconds)
        except sqlite3.Error:
            # Never let journal maintenance stop the server from starting.
            logger.debug("Execution reconciliation unavailable", exc_info=True)
            return (0, 0)
        if counts["live"]:
            # The lock is held, so a worker is alive and owns these rows.
            logger.debug("Execution reconciliation skipped: work in progress")
            return (0, 0)
        cleared = counts["cleared"] + counts["released"]
        held = counts["held"]
        if cleared or held:
            logger.warning(
                "Reconciled %d %s execution(s): %d cleared, %d released after the session ended, %d need operator verification",
                cleared + held,
                self.product,
                counts["cleared"],
                counts["released"],
                held,
            )
        return (cleared, held)

    def no_live_worker(self):
        """True only when no worker of any process holds this journal's lock."""
        try:
            with worker_lock(self.path):
                return True
        except OSError:
            return False

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
                if row["owner"] == self.owner and self._has_live_worker(execution_id):
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
        # Also here, not only at startup: the connector's server is long-lived,
        # so a row orphaned by some other instance would otherwise block it
        # until the next restart. Outside every lock below â€” reconciliation
        # takes worker_lock itself and must not do so inside a transaction.
        self.reconcile_orphans()
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
                        "SELECT id, state, started, model_guid FROM executions"
                        " WHERE state IN ('running', 'unknown') LIMIT 1"
                    ).fetchone()
                    if active:
                        # Name what is blocking and what ends it. A customer who
                        # only hears "unfinished execution" reasonably concludes
                        # their installation is broken and reinstalls RFEM,
                        # which cannot help: the journal is outside it.
                        return {
                            "execution_id": execution_id,
                            "state": "busy",
                            "active_execution_id": active["id"],
                            "active_state": active["state"],
                            "active_started": active["started"],
                            "active_model_guid": active["model_guid"],
                            "error": _blocked_reason(active),
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
                    state, error = "unknown", UNCLASSIFIED_FAILURE
                    record = _Recorder(self, execution_id, context)
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
                            work(capture, record)
                        state, error = "succeeded", ""
                    except Exception as exc:
                        logger.debug("RFEM script failed", exc_info=True)
                        state, error = _failure_details(exc, record.mutation_attempted)
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
                    # Run in the submitting request's context: the worker opens the
                    # product connection, which reads the caller's MCP request (its
                    # client application). Threads start with an empty one.
                    thread = threading.Thread(
                        target=contextvars.copy_context().run,
                        args=(run,),
                        daemon=True,
                        name="rfem-execution",
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


RESULT_LABELS = {"rfem": "RFEM", "rstab": "RSTAB", "rsection": "RSECTION"}


def result_text(result, product="rfem"):
    """The text a client reads. The marker prefix (Output:, TimeoutError:,
    RuntimeError:) is parsed by the Dlubal Connector and CALC and must not
    change; the label names the product that ran the operation."""
    state = result["state"]
    marker = (
        "Output:"
        if state == "succeeded"
        else "TimeoutError:"
        if state == "running"
        else "RuntimeError:"
    )
    summary = {key: value for key, value in result.items() if key != "output"}
    label = RESULT_LABELS.get(product, "RFEM")
    return (
        f"{marker} {label} execution {json.dumps(summary)}\n{result.get('output', '')}"
    )


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
