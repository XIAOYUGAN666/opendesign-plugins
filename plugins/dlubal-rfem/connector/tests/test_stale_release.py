"""A stale record releases itself once its application session has ended.

The record blocks new work because a partial change may sit in the memory of
the application that ran it. When that session is gone, the model reopens from
its last save and the lock protects nothing, while every CALC chat on the
machine and every Connector update stays refused (2026-09-18 to 09-23: one
colleague blocked for five days, Setup deferred on another machine).
"""

import os
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

from dlubal_mcp.core import server
from dlubal_mcp.core.config import DlubalMCPSettings
from dlubal_mcp.core.operation_control import OperationControl
from dlubal_mcp.rfem import stale_release
from dlubal_mcp.rfem.execution import ExecutionManager, worker_lock


@pytest.fixture
def journal(tmp_path):
    return tmp_path / "rfem-executions.sqlite3"


def leave(path, state, mutated=1, age=600.0, execution_id=None):
    """Write exactly what an interrupted task leaves behind."""
    execution_id = execution_id or str(uuid4())
    db = sqlite3.connect(str(path))
    with db:
        db.execute(
            "INSERT INTO executions (id, fingerprint, owner, context, state, endpoint, started, mutated)"
            " VALUES (?, 'fp', 'dead-owner', '', ?, '127.0.0.1:9000', ?, ?)",
            (execution_id, state, time.time() - age, mutated),
        )
    db.close()
    return execution_id


def row(path, execution_id):
    db = sqlite3.connect(str(path))
    try:
        return db.execute(
            "SELECT state, error FROM executions WHERE id=?", (execution_id,)
        ).fetchone()
    finally:
        db.close()


def rfem_restarted(monkeypatch, ago=5.0):
    """Every RFEM process started after the operation (which began 600 s ago)."""
    monkeypatch.setattr(
        stale_release, "start_times", lambda prefix: [time.time() - ago]
    )


def test_unknown_record_is_released_once_the_session_ended(journal, monkeypatch):
    ExecutionManager(journal)
    stuck = leave(journal, "unknown")
    rfem_restarted(monkeypatch)

    assert stale_release.release(journal, "rfem")["released"] == 1
    state, error = row(journal, stuck)
    assert state == "failed"
    assert "RFEM session that ran this operation has ended" in error
    assert "re-read the model" in error


def test_release_keeps_the_original_unknown_result_readable(journal, monkeypatch):
    ExecutionManager(journal)
    stuck = leave(journal, "unknown")
    db = sqlite3.connect(str(journal))
    with db:
        db.execute(
            "UPDATE executions SET error='gRPC DEADLINE_EXCEEDED during set_object_list' WHERE id=?",
            (stuck,),
        )
    db.close()
    rfem_restarted(monkeypatch)

    stale_release.release(journal, "rfem")
    error = row(journal, stuck)[1]
    assert error.startswith("Released automatically")
    assert (
        "Previous state unknown: gRPC DEADLINE_EXCEEDED during set_object_list" in error
    )


def test_released_record_stops_blocking_new_work(journal, monkeypatch):
    ExecutionManager(journal)
    leave(journal, "unknown")
    rfem_restarted(monkeypatch)

    result = ExecutionManager(journal).submit("", "ctx", "fp-new", lambda *_: None)
    assert result["state"] == "succeeded"


def test_released_record_is_never_replayed(journal, monkeypatch):
    """The tombstone survives: the same ID reports its record, it never reruns."""
    manager = ExecutionManager(journal)
    stuck = leave(journal, "unknown")
    rfem_restarted(monkeypatch)
    manager.reconcile_orphans()

    result = manager.submit(stuck, "", "fp", lambda *_: pytest.fail("replayed"))
    assert result["state"] == "failed"


def test_abandoned_write_is_released_directly_when_the_session_ended(
    journal, monkeypatch
):
    ExecutionManager(journal)
    stuck = leave(journal, "running", mutated=1)
    rfem_restarted(monkeypatch)

    manager = ExecutionManager(journal)  # startup reconciles
    assert row(journal, stuck)[0] == "failed"
    assert manager.reconcile_orphans() == (0, 0)


def test_record_keeps_blocking_while_the_session_is_open(journal, monkeypatch):
    """RFEM started BEFORE the operation may still hold its partial change."""
    ExecutionManager(journal)
    stuck = leave(journal, "unknown")
    monkeypatch.setattr(
        stale_release, "start_times", lambda prefix: [time.time() - 3600]
    )

    assert stale_release.release(journal, "rfem")["released"] == 0
    assert row(journal, stuck)[0] == "unknown"
    result = ExecutionManager(journal).submit("", "ctx", "fp-new", lambda *_: None)
    assert result["state"] == "busy"
    assert "close RFEM" in result["error"]


def test_one_old_session_among_new_ones_keeps_blocking(journal, monkeypatch):
    ExecutionManager(journal)
    stuck = leave(journal, "unknown")
    monkeypatch.setattr(
        stale_release, "start_times", lambda prefix: [time.time(), time.time() - 3600]
    )

    stale_release.release(journal, "rfem")
    assert row(journal, stuck)[0] == "unknown"


def test_unreadable_process_list_keeps_blocking(journal):
    """The autouse fixture reports None: nothing proven, nothing released."""
    ExecutionManager(journal)
    stuck = leave(journal, "unknown")

    assert stale_release.release(journal, "rfem")["released"] == 0
    assert row(journal, stuck)[0] == "unknown"


def test_no_running_application_counts_as_ended(journal, monkeypatch):
    ExecutionManager(journal)
    stuck = leave(journal, "unknown")
    monkeypatch.setattr(stale_release, "start_times", lambda prefix: [])

    stale_release.release(journal, "rfem")
    assert row(journal, stuck)[0] == "failed"


def test_session_is_judged_per_product(journal, monkeypatch):
    seen = []
    monkeypatch.setattr(
        stale_release, "start_times", lambda prefix: seen.append(prefix) or []
    )
    ExecutionManager(journal, product="rsection")
    leave(journal, "unknown")

    stale_release.release(journal, "rsection")
    assert seen == ["rsection"]


def test_live_worker_blocks_release(journal, monkeypatch):
    ExecutionManager(journal)
    stuck = leave(journal, "unknown")
    rfem_restarted(monkeypatch)

    with worker_lock(journal):
        assert stale_release.release(journal, "rfem")["live"] is True
    assert row(journal, stuck)[0] == "unknown"


def test_healthy_journal_never_takes_the_worker_lock(journal):
    """A worker that cannot take the lock fails; polling must not cause that."""
    ExecutionManager(journal)
    leave(journal, "succeeded")
    leave(journal, "running", age=0.0)  # admitted, inside the grace window

    with worker_lock(journal):
        assert stale_release.release(journal, "rfem")["live"] is False


def test_pre_1_0_15_journal_is_read_without_migrating_it(journal, monkeypatch):
    """The Setup helper may meet an older server's journal: never alter it."""
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

    assert stale_release.release(journal, "rfem")["held"] == 1  # treated as a write
    columns = {
        r[1]
        for r in sqlite3.connect(str(journal)).execute("PRAGMA table_info(executions)")
    }
    assert "mutated" not in columns


def test_standalone_script_runs_without_the_package(journal, tmp_path):
    """The Setup helper runs this file with RFEM's Python and no dlubal_mcp."""
    ExecutionManager(journal)
    stuck = leave(journal, "unknown")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            stale_release.__file__,
            "--state-file",
            str(journal),
            "--product",
            "rfem",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert '"live": false' in result.stdout
    if os.name != "nt":
        assert row(journal, stuck)[0] == "unknown"


def test_standalone_script_reports_a_missing_journal(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            stale_release.__file__,
            "--state-file",
            str(tmp_path / "none"),
            "--product",
            "rfem",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert not (tmp_path / "none").exists()


@pytest.mark.skipif(os.name != "nt", reason="reads the Windows process table")
def test_real_process_table_sees_this_interpreter():
    image = os.path.basename(sys.executable).lower()[:6]
    times = stale_release._process_start_times(image)
    assert times, "the running interpreter must be listed"
    assert all(created < time.time() for created in times)


def test_opted_in_stop_is_not_blocked_by_an_unresolved_record(journal):
    control = OperationControl()
    ExecutionManager(journal, control=control)
    leave(journal, "unknown")

    assert control.prepare_stop()["state"] == "unknown"  # default is unchanged
    permit = control.prepare_stop(allow_unresolved=True)
    assert permit["state"] == "draining" and len(permit["lease"]) == 64
    assert permit["operations"][0]["state"] == "unknown"
    # The record is still there and still blocks new work after the stop.
    result = ExecutionManager(journal).submit("", "ctx", "fp-new", lambda *_: None)
    assert result["state"] == "busy"


def test_opted_in_stop_refuses_while_another_process_holds_the_worker_lock(journal):
    """'unknown' in this process may be LIVE work of another one."""
    control = OperationControl()
    ExecutionManager(journal, control=control)
    leave(journal, "unknown")

    with worker_lock(journal):
        assert "lease" not in control.prepare_stop(allow_unresolved=True)
    assert "lease" in control.prepare_stop(allow_unresolved=True)


def test_opted_in_stop_still_waits_for_live_work_and_readers(journal):
    import threading

    control = OperationControl()
    manager = ExecutionManager(journal, control=control)
    with control.reading():
        assert "lease" not in control.prepare_stop(allow_unresolved=True)
    entered, finish = threading.Event(), threading.Event()

    def work(capture, record):
        entered.set()
        assert finish.wait(3)

    try:
        manager.submit(str(uuid4()), "chat", "input", work, wait_seconds=0)
        assert entered.wait(1)
        assert "lease" not in control.prepare_stop(allow_unresolved=True)
    finally:
        finish.set()


def test_control_endpoint_releases_before_answering(monkeypatch, tmp_path):
    """No new submission is needed: the Connector's poll or Setup releases it."""
    journal = tmp_path / "rfem-executions.sqlite3"
    control = OperationControl()
    ExecutionManager(journal, control=control)
    stuck = leave(journal, "unknown")
    monkeypatch.setattr(server, "_build_mcp", lambda *_: FastMCP("control-test"))
    monkeypatch.setattr(server, "OperationControl", lambda: control, raising=False)
    token = "control-test-" + "a" * 48
    app = server.create_asgi_app(DlubalMCPSettings(allowed_api_keys=token))
    rfem_restarted(monkeypatch)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + token}
        body = client.post(
            "/control",
            headers=headers,
            json={"action": "prepare_stop", "allow_unresolved": "1"},
        ).json()
    assert row(journal, stuck)[0] == "failed"
    assert body["state"] == "draining"
