"""Abandoned executions must not block the machine forever.

A 'running' row is closed by the process that opened it. When that process dies
nobody writes the ending, and because the journal is shared across servers, the
stale row refuses every later execution from every instance — including the
connector's, behind a green status badge.
"""

import sqlite3
import time

import pytest

from dlubal_mcp.rfem.execution import ExecutionManager, worker_lock


@pytest.fixture
def journal(tmp_path):
    return tmp_path / "rfem-executions.sqlite3"


def abandon(path, execution_id="stale", mutated=0, age=600.0):
    """Write exactly what a killed process leaves behind."""
    db = sqlite3.connect(str(path))
    with db:
        db.execute(
            "INSERT INTO executions (id, fingerprint, owner, context, state, endpoint, started, mutated)"
            " VALUES (?, 'fp', 'dead-owner', '', 'running', '127.0.0.1:9000', ?, ?)",
            (execution_id, time.time() - age, mutated),
        )
    db.close()


def state_of(path, execution_id="stale"):
    db = sqlite3.connect(str(path))
    try:
        return db.execute(
            "SELECT state FROM executions WHERE id=?", (execution_id,)
        ).fetchone()[0]
    finally:
        db.close()


def test_orphan_that_never_wrote_is_cleared(journal):
    manager = ExecutionManager(journal)
    abandon(journal, mutated=0)

    assert manager.reconcile_orphans() == (1, 0)
    assert state_of(journal) == "failed"


def test_orphan_that_wrote_is_held_for_an_operator(journal):
    """A half-applied model change must be seen by a person, not auto-cleared."""
    manager = ExecutionManager(journal)
    abandon(journal, mutated=1)

    assert manager.reconcile_orphans() == (0, 1)
    assert state_of(journal) == "unknown"


def test_a_cleared_orphan_stops_blocking_new_work(journal):
    """The whole point: the next execution is admitted instead of refused."""
    ExecutionManager(journal)
    abandon(journal, mutated=0)

    manager = ExecutionManager(journal)
    result = manager.submit("", "ctx", "fp-new", lambda capture, record: None)
    assert result["state"] == "succeeded"


def test_a_held_orphan_still_blocks_new_work(journal):
    ExecutionManager(journal)
    abandon(journal, mutated=1)

    manager = ExecutionManager(journal)
    result = manager.submit("", "ctx", "fp-new", lambda capture, record: None)
    assert result["state"] == "busy"


def test_a_refusal_says_what_blocks_it_and_how_to_clear_it(journal):
    """"Unfinished execution" alone sends customers to reinstall RFEM."""
    ExecutionManager(journal)
    abandon(journal, mutated=1)

    result = ExecutionManager(journal).submit(
        "", "ctx", "fp-new", lambda capture, record: None
    )
    assert result["active_state"] == "unknown"
    assert result["active_execution_id"] == "stale"
    assert "active_started" in result and "active_model_guid" in result
    assert "stale" in result["error"]
    assert "Inspect the model in RFEM" in result["error"]
    assert "Reinstalling RFEM" in result["error"]


def test_a_refusal_for_live_work_tells_the_caller_to_wait(journal):
    ExecutionManager(journal)
    abandon(journal, mutated=0, age=0.0)  # inside the grace window: treated as live

    result = ExecutionManager(journal).submit(
        "", "ctx", "fp-new", lambda capture, record: None
    )
    assert result["state"] == "busy"
    assert result["active_state"] == "running"
    assert "still executing" in result["error"]


def test_live_work_elsewhere_is_never_reconciled(journal):
    """The worker lock is the proof. Held means a worker is alive: hands off."""
    ExecutionManager(journal)
    abandon(journal, mutated=0)

    with worker_lock(journal):
        assert ExecutionManager(journal).reconcile_orphans() == (0, 0)
    assert state_of(journal) == "running"


def test_a_freshly_admitted_row_is_left_alone(journal):
    """Grace period: a worker may not have reached its lock acquisition yet."""
    ExecutionManager(journal)
    abandon(journal, mutated=0, age=0.0)

    assert ExecutionManager(journal).reconcile_orphans() == (0, 0)
    assert state_of(journal) == "running"


def test_startup_reconciles_without_being_asked(journal):
    ExecutionManager(journal)
    abandon(journal, mutated=0)

    ExecutionManager(journal)
    assert state_of(journal) == "failed"


def test_pre_1_0_15_journals_are_migrated_and_treated_as_uncertain(journal):
    """An old row carries no mutation record, so it cannot be proven harmless."""
    db = sqlite3.connect(str(journal))
    with db:
        db.execute("""CREATE TABLE executions (
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, owner TEXT NOT NULL,
            context TEXT NOT NULL, state TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '', model_guid TEXT, endpoint TEXT NOT NULL,
            output_available INTEGER NOT NULL DEFAULT 1, started REAL NOT NULL, finished REAL)""")
        db.execute(
            "INSERT INTO executions (id, fingerprint, owner, context, state, endpoint, started)"
            " VALUES ('legacy', 'fp', 'dead', '', 'running', '127.0.0.1:9000', ?)",
            (time.time() - 600,),
        )
    db.close()

    ExecutionManager(journal)
    assert state_of(journal, "legacy") == "unknown"


def test_mutation_marking_survives_the_process_that_recorded_it(journal):
    """What reconciliation relies on: the flag is in the journal, not memory."""
    manager = ExecutionManager(journal)
    seen = {}

    def work(capture, record):
        seen["recorder"] = record
        record.mutated()

    manager.submit("", "ctx", "fp", work)
    assert hasattr(seen["recorder"], "mutated")

    db = sqlite3.connect(str(journal))
    try:
        assert db.execute("SELECT mutated FROM executions").fetchone()[0] == 1
    finally:
        db.close()
