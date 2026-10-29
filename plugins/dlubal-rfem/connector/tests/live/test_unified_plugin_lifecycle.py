"""Deterministic safety checks for the opt-in live RFEM driver; no vendor API."""

from types import SimpleNamespace
from typing import Any

import pytest
from test_rfem_complete_contract import _terminal


class JournalClient:
    def __init__(self, state: str, observation=None) -> None:
        self.state = state
        self.observation = observation
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def call_tool(self, name, arguments, **_kwargs):
        self.calls.append((name, arguments))
        if self.observation is not None:
            asyncio.get_running_loop().call_soon(_fixture_heartbeat, self.observation)
        return SimpleNamespace(
            structured_content={
                "state": self.state,
                "execution_id": arguments["execution_id"],
            }
        )


@pytest.mark.parametrize("state", ["unknown", "not_found", "rejected", "unverifiable"])
async def test_terminal_refuses_unsettled_execution_without_replay(state):
    client = JournalClient(state)
    with pytest.raises(RuntimeError, match="unsettled"):
        await _terminal(client, "same-dispatched-id")
    assert client.calls == [
        ("get_rfem_execution", {"execution_id": "same-dispatched-id"})
    ]


import asyncio
import json
import math
import sys
import time
import zipfile
from uuid import uuid4

import test_rfem_complete_contract as driver
from test_rfem_complete_contract import (
    DriverJournal,
    LifecycleObservation,
    _assert_deformations,
    _bound_archive,
    _drain_stderr,
    _hold_until_safe,
    _readback_script,
    _safe_to_shutdown,
)


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows embedded Python path contract"
)
def test_live_driver_uses_same_verified_directory_under_short_windows_alias(tmp_path):
    from pathlib import Path

    long_root = tmp_path / ("nested-artifact-" * 7) / ("connector-root-" * 6)
    extended_root = Path("\\\\?\\" + str(long_root))
    extended_root.mkdir(parents=True)
    (extended_root / "sentinel.txt").write_text("same verified bytes", encoding="utf-8")
    process_root = getattr(driver, "_artifact_process_root", None)
    assert callable(process_root), (
        "live driver lacks the Host-equivalent Windows launch path normalization"
    )
    actual = process_root(extended_root)
    assert actual.samefile(extended_root)
    assert len(str(actual)) < len(str(extended_root))
    assert (actual / "sentinel.txt").read_text("utf-8") == "same verified bytes"


def _fixture_heartbeat(observation):
    observation.sequence += 1
    observation.observed_at = time.monotonic()
    observation.changed.set()


async def _pending():
    await asyncio.Event().wait()


@pytest.mark.parametrize(
    "state,safe",
    [
        ("succeeded", True),
        ("failed", True),
        ("running", False),
        ("unknown", False),
        ("not_found", False),
        ("rejected", False),
    ],
)
async def test_shutdown_requires_exact_terminal_journal_and_fresh_idle(
    tmp_path, state, safe
):
    journal = DriverJournal(tmp_path, {"id": {"state": "dispatched"}})
    observation = LifecycleObservation("idle", 0, time.monotonic())
    reader = asyncio.create_task(_pending())
    try:
        assert (
            await _safe_to_shutdown(
                JournalClient(state, observation), journal, observation, reader
            )
            is safe
        )
        observation.observed_at -= 6
        assert not await _safe_to_shutdown(
            JournalClient("succeeded"), DriverJournal(tmp_path), observation, reader
        )
        observation.observed_at = time.monotonic()
        observation.in_flight = 1
        assert not await _safe_to_shutdown(
            JournalClient("succeeded"), DriverJournal(tmp_path), observation, reader
        )
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


async def test_dispatch_records_exact_id_before_lost_response_and_refuses_replay(
    tmp_path,
):
    journal = DriverJournal(tmp_path)
    reader = asyncio.create_task(_pending())
    journal.observation = LifecycleObservation("idle", 0, time.monotonic())
    journal.lifecycle_task = reader

    class LostResponse(JournalClient):
        async def call_tool(self, name, arguments, **kwargs):
            if name == "run_rfem_script":
                recorded = json.loads((tmp_path / "driver-journal.json").read_text())
                assert recorded["executions"]["exact-id"]["state"] == "dispatched"
                self.calls.append((name, arguments))
                raise ConnectionError("controlled lost response")
            return await super().call_tool(name, arguments, **kwargs)

    client = LostResponse("unknown", journal.observation)
    try:
        asyncio.get_running_loop().call_soon(_fixture_heartbeat, journal.observation)
        with pytest.raises(ConnectionError):
            await journal.dispatch(
                client, "run_rfem_script", {"execution_id": "exact-id"}
            )
        assert not await _safe_to_shutdown(client, journal, journal.observation, reader)
        with pytest.raises(RuntimeError, match="unsettled"):
            await journal.dispatch(
                client, "run_rfem_script", {"execution_id": "exact-id"}
            )
        assert [name for name, _ in client.calls].count("run_rfem_script") == 1
        client.state = "succeeded"
        with pytest.raises(RuntimeError, match="replay"):
            await journal.dispatch(
                client, "run_rfem_script", {"execution_id": "exact-id"}
            )
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.parametrize("failure", ["query", "identity", "reader"])
async def test_shutdown_failure_retains_state(tmp_path, failure):
    class BrokenQuery(JournalClient):
        async def call_tool(self, name, arguments, **kwargs):
            if failure == "query":
                raise ConnectionError("controlled local failure")
            body = await super().call_tool(name, arguments, **kwargs)
            body.structured_content["execution_id"] = "wrong-id"
            return body

    journal = DriverJournal(tmp_path, {"exact-id": {"state": "dispatched"}})
    reader = asyncio.create_task(_pending())
    if failure == "reader":
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
    try:
        assert not await _safe_to_shutdown(
            BrokenQuery("succeeded"),
            journal,
            LifecycleObservation("idle", 0, time.monotonic()),
            reader,
        )
        assert "exact-id" in journal.executions
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.parametrize(
    "interruption", [EOFError, KeyboardInterrupt, asyncio.CancelledError]
)
async def test_unknown_hold_keeps_real_process_readers_and_loop_alive(
    tmp_path, monkeypatch, interruption
):
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-c",
        "import sys,time; print('owned',flush=True); sys.stderr.write('private diagnostic\\n'); sys.stderr.flush(); time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert child.stdout and child.stderr
    assert (await child.stdout.readline()).strip() == b"owned"
    stderr = bytearray()
    stderr_reader = asyncio.create_task(_drain_stderr(child.stderr, stderr))
    lifecycle_reader = asyncio.create_task(_pending())
    journal = DriverJournal(
        tmp_path, {"exact-id": {"state": "dispatched"}}, stderr_task=stderr_reader
    )
    observation = LifecycleObservation("idle", 0, time.monotonic())
    client = JournalClient("unknown", observation)
    prompted = asyncio.Event()
    released = asyncio.Event()
    notices: list[str] = []
    attempts = 0

    async def prompt():
        nonlocal attempts
        attempts += 1
        prompted.set()
        if attempts == 1:
            raise interruption()
        await released.wait()

    async def local_pause(_seconds):
        await released.wait()

    # No timer polling in the test; operator resolution releases a local barrier.
    monkeypatch.setattr(driver.asyncio, "sleep", local_pause)

    async def check():
        return await _safe_to_shutdown(client, journal, observation, lifecycle_reader)

    hold = asyncio.create_task(_hold_until_safe(check, prompt, notices.append))
    try:
        await asyncio.wait_for(prompted.wait(), 2)
        assert not hold.done() and child.returncode is None
        assert not lifecycle_reader.done() and not stderr_reader.done()
        assert (
            tmp_path.is_dir() and journal.executions["exact-id"]["state"] == "unknown"
        )
        # Simulates the operator's existing CLI resolution, never an automatic
        # call by the driver. Only the same journal ID is subsequently queried.
        client.state = "failed"
        observation.observed_at = time.monotonic()
        released.set()
        await asyncio.wait_for(hold, 2)
        assert child.returncode is None
        assert all(
            name == "get_rfem_execution" and args["execution_id"] == "exact-id"
            for name, args in client.calls
        )
        assert notices
    finally:
        if not hold.done():
            client.state = "failed"
            released.set()
            await hold
        child.terminate()
        await child.wait()
        await stderr_reader
        lifecycle_reader.cancel()
        await asyncio.gather(lifecycle_reader, return_exceptions=True)


def test_archive_binding_uses_exact_input_and_rejects_changed_extraction(tmp_path):
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "release.json").write_text('{"version":"test"}')
    archive = tmp_path / "exact.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.write(extracted / "release.json", "release.json")
    assert len(_bound_archive(extracted, archive)) == 64
    (extracted / "release.json").write_text('{"version":"changed"}')
    with pytest.raises(ValueError, match="artifactExtractionMismatch"):
        _bound_archive(extracted, archive)


def test_postcondition_script_uses_locked_sdk_and_exact_model():
    from dlubal.api import rfem

    from dlubal_rfem_mcp.core.sandbox import (
        execute_script,
        script_builtins,
        validate_code_ast,
    )

    guid = str(uuid4())
    objects = [
        rfem.structure_core.Node(no=1),
        rfem.structure_core.Node(no=2, coordinate_1=6),
        rfem.structure_core.Line(no=1, definition_nodes=[1, 2]),
        rfem.structure_core.Member(no=1, line=1, cross_section_start=1),
        rfem.structure_core.Material(no=1, name="S235"),
        rfem.structure_core.CrossSection(no=1, name="IPE 240", material=1),
        rfem.types_for_nodes.NodalSupport(
            no=901,
            nodes=[1],
            spring_x=math.inf,
            spring_y=math.inf,
            spring_z=math.inf,
            rotational_restraint_x=math.inf,
            rotational_restraint_y=math.inf,
            rotational_restraint_z=math.inf,
        ),
        rfem.loading.StaticAnalysisSettings(
            no=901,
            analysis_type=rfem.loading.StaticAnalysisSettings.ANALYSIS_TYPE_GEOMETRICALLY_LINEAR,
        ),
        rfem.loading.LoadCase(
            no=901, name="LC901", self_weight_active=False, static_analysis_settings=901
        ),
        rfem.loads.MemberLoad(
            no=901,
            members=[1],
            load_case=901,
            magnitude=-10000,
            load_type=rfem.loads.MemberLoad.LOAD_TYPE_FORCE,
            load_distribution=rfem.loads.MemberLoad.LOAD_DISTRIBUTION_UNIFORM,
            load_direction=rfem.loads.MemberLoad.LOAD_DIRECTION_GLOBAL_Z_OR_USER_DEFINED_W_TRUE_LENGTH,
        ),
    ]
    calls = []

    class App:
        def get_application_info(self):
            return SimpleNamespace(full_version="6.14.0013.249.vendorhash")

        def get_active_model(self):
            return SimpleNamespace(guid=guid)

        def get_object(self, request):
            calls.append(request)
            return next(
                item
                for item in objects
                if type(item) is type(request) and item.no == request.no
            )

    code = _readback_script("SAFE_MARKER", guid)
    assert validate_code_ast(code) is None
    output: list[str] = []
    execute_script(
        code,
        {
            "__builtins__": script_builtins(output.append),
            "app": App(),
            "rfem": rfem,
            "math": math,
        },
    )
    assert "EXACT_BUILD_AND_MODEL_READBACK_OK" in output
    assert len(calls) == 10
    objects[1].coordinate_1 = 5
    with pytest.raises(AssertionError):
        execute_script(
            code,
            {
                "__builtins__": script_builtins(output.append),
                "app": App(),
                "rfem": rfem,
                "math": math,
            },
        )


@pytest.mark.parametrize("uz", [0, 1, -1e-15, math.inf, math.nan, None])
def test_deformation_postcondition_rejects_nonphysical_or_unavailable_result(uz):
    with pytest.raises(AssertionError):
        _assert_deformations(
            {
                "status": "ok",
                "has_results": True,
                "source": {"model_guid": "guid"},
                "units": {"u_z": "m"},
                "rows": [{"node_no": 2, "loading": "LC901", "u_z": uz}],
            },
            "guid",
        )


def test_deformation_postcondition_requires_node_loading_guid_and_metres():
    body: dict[str, Any] = {
        "status": "ok",
        "has_results": True,
        "source": {"model_guid": "guid"},
        "units": {"u_z": "m"},
        "rows": [{"node_no": 2, "loading": "LC901", "u_z": -0.01}],
    }
    assert _assert_deformations(body, "guid") == -0.01
    for section, field, wrong in [
        ("source", "model_guid", "other"),
        ("units", "u_z", "mm"),
    ]:
        changed = {**body, section: {**body[section], field: wrong}}
        with pytest.raises(AssertionError):
            _assert_deformations(changed, "guid")
    for field, wrong_row_value in [("node_no", 1), ("loading", "LC902")]:
        with pytest.raises(AssertionError):
            _assert_deformations(
                {**body, "rows": [{**body["rows"][0], field: wrong_row_value}]}, "guid"
            )


async def test_engineering_dispatch_refuses_failed_reader_before_sending_any_id(
    tmp_path,
):
    reader = asyncio.create_task(_pending())
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    journal = DriverJournal(
        tmp_path,
        observation=LifecycleObservation("idle", 0, time.monotonic()),
        lifecycle_task=reader,
    )
    client = JournalClient("succeeded")
    with pytest.raises(RuntimeError, match="unsettled lifecycle"):
        await journal.dispatch(
            client, "write_rfem_model", {"execution_id": "never-dispatched"}
        )
    assert not client.calls and not journal.executions


@pytest.mark.parametrize("age", [0.0, 6.0])
async def test_startup_dispatch_waits_for_a_new_owned_idle_sample(tmp_path, age):
    reader = asyncio.create_task(_pending())
    observation = LifecycleObservation("idle", 0, time.monotonic() - age, sequence=7)
    journal = DriverJournal(tmp_path, observation=observation, lifecycle_task=reader)
    client = JournalClient("succeeded")
    pending = asyncio.create_task(
        journal.dispatch(client, "write_rfem_model", {"execution_id": "one-new-id"})
    )
    try:
        await asyncio.sleep(0)
        assert not pending.done(), (
            "cached startup idle cannot replace a post-read heartbeat"
        )
        assert not client.calls and not journal.executions
        _fixture_heartbeat(observation)
        await pending
        assert client.calls == [("write_rfem_model", {"execution_id": "one-new-id"})]
        assert list(journal.executions) == ["one-new-id"]
    finally:
        if not pending.done():
            pending.cancel()
        reader.cancel()
        await asyncio.gather(pending, reader, return_exceptions=True)


async def test_startup_dispatch_unknown_remains_refused(tmp_path):
    reader = asyncio.create_task(_pending())
    journal = DriverJournal(
        tmp_path,
        observation=LifecycleObservation("unknown", 0, time.monotonic()),
        lifecycle_task=reader,
    )
    client = JournalClient("succeeded")
    try:
        with pytest.raises(RuntimeError, match="unsettled lifecycle"):
            await journal.dispatch(
                client, "write_rfem_model", {"execution_id": "never-dispatched"}
            )
        assert not client.calls and not journal.executions
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


def test_interactive_entry_refuses_both_first_and_second_interrupt(
    monkeypatch, tmp_path
):
    import signal

    async def controlled(_root, _credential, _archive):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-B", "-c", "import time; time.sleep(30)"
        )
        try:
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
            await asyncio.sleep(0.02)
            assert process.returncode is None
            return {"safeControlledOwner": True}
        finally:
            process.terminate()
            await process.wait()

    monkeypatch.setattr(driver, "run_contract", controlled)
    prior = signal.getsignal(signal.SIGINT)
    assert driver._run_interactive(
        tmp_path, "not-a-credential", tmp_path / "unused.zip"
    ) == {"safeControlledOwner": True}
    assert signal.getsignal(signal.SIGINT) == prior


async def test_stderr_reader_is_bounded_and_detects_private_values_even_before_tail_rollover():
    reader = asyncio.StreamReader()
    captured = bytearray()
    reader.feed_data(b"x" * 80000)
    reader.feed_eof()
    await _drain_stderr(reader, captured)
    assert len(captured) == 65536
    reader = asyncio.StreamReader()
    reader.feed_data(b"first private-credential then diagnostic")
    reader.feed_eof()
    with pytest.raises(RuntimeError, match="private diagnostic boundary violation"):
        await _drain_stderr(reader, bytearray(), (b"private-credential",))


def test_secure_key_prompt_rejects_redirected_input_and_echo_fallback(monkeypatch):
    import getpass
    import warnings

    monkeypatch.setattr(driver.sys.stdin, "isatty", lambda: False)

    def forbidden_prompt(*_args):
        raise AssertionError("redirected input must never read a key")

    monkeypatch.setattr(driver.getpass, "getpass", forbidden_prompt)
    with pytest.raises(RuntimeError, match="interactive terminal"):
        driver._read_credential()
    monkeypatch.setattr(driver.sys.stdin, "isatty", lambda: True)

    def echo_fallback(*_args):
        warnings.warn("controlled no-echo unavailable", getpass.GetPassWarning)
        raise AssertionError("getpass fallback must never reach credential input")

    monkeypatch.setattr(driver.getpass, "getpass", echo_fallback)
    with pytest.raises(getpass.GetPassWarning):
        driver._read_credential()


async def test_predispatch_idle_and_terminal_journal_require_a_new_issued_idle(
    tmp_path,
):
    reader = asyncio.create_task(_pending())
    journal = DriverJournal(
        tmp_path, {"same-id": {"state": "dispatched", "dispatchSequence": 7}}
    )
    observation = LifecycleObservation("idle", 0, time.monotonic(), sequence=7)
    queried = asyncio.Event()

    class TerminalQuery(JournalClient):
        async def call_tool(self, name, arguments, **kwargs):
            result = await super().call_tool(name, arguments, **kwargs)
            queried.set()
            return result

    check = asyncio.create_task(
        _safe_to_shutdown(TerminalQuery("succeeded"), journal, observation, reader)
    )
    try:
        await queried.wait()
        await asyncio.sleep(0)
        assert not check.done(), (
            "pre-dispatch idle cannot establish post-operation safety"
        )
        _fixture_heartbeat(observation)
        assert await check is True
    finally:
        if not check.done():
            check.cancel()
        reader.cancel()
        await asyncio.gather(check, reader, return_exceptions=True)


async def test_startup_failed_actual_child_exit_is_safe_only_without_any_dispatch(
    tmp_path,
):
    journal = DriverJournal(tmp_path)
    journal.save()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-B", "-c", "raise SystemExit(3)"
    )
    await process.wait()
    assert process.returncode == 3
    assert await driver._startup_exited_without_dispatch(process, journal)
    assert (tmp_path / "driver-journal.json").is_file(), (
        "failed startup evidence remains"
    )
    journal.executions["lost-response-id"] = {"state": "dispatched"}
    journal.save()
    assert not await driver._startup_exited_without_dispatch(process, journal), (
        "dead child cannot settle a possibly dispatched engineering action"
    )


@pytest.mark.parametrize("stage", ["journal-query", "next-heartbeat"])
async def test_stderr_eof_during_safety_await_blocks_new_engineering_dispatch(
    tmp_path, stage
):
    lifecycle = asyncio.create_task(_pending())
    stderr_stream = asyncio.StreamReader()
    stderr_reader = asyncio.create_task(_drain_stderr(stderr_stream, bytearray()))
    observation = LifecycleObservation("idle", 0, time.monotonic())
    journal = DriverJournal(
        tmp_path,
        {"prior-id": {"state": "dispatched"}},
        observation,
        lifecycle,
        stderr_reader,
    )
    queried = asyncio.Event()
    release_query = asyncio.Event()

    class DelayedJournal(JournalClient):
        async def call_tool(self, name, arguments, **kwargs):
            if name == "get_rfem_execution":
                queried.set()
                if stage == "journal-query":
                    await release_query.wait()
            return await super().call_tool(name, arguments, **kwargs)

    client = DelayedJournal("succeeded")
    dispatch = asyncio.create_task(
        journal.dispatch(
            client, "write_rfem_model", {"execution_id": "must-not-dispatch"}
        )
    )
    try:
        await queried.wait()
        stderr_stream.feed_eof()
        await stderr_reader
        release_query.set()
        await asyncio.sleep(0)
        _fixture_heartbeat(observation)
        with pytest.raises(RuntimeError, match="unsettled lifecycle"):
            await dispatch
        assert all(name == "get_rfem_execution" for name, _ in client.calls)
        assert "must-not-dispatch" not in journal.executions
    finally:
        release_query.set()
        if not dispatch.done():
            dispatch.cancel()
        lifecycle.cancel()
        await asyncio.gather(dispatch, lifecycle, return_exceptions=True)


@pytest.mark.parametrize("still_open", [False, True])
def test_cleanup_requires_model_absence_after_successful_close_ack(still_open):
    from dlubal_rfem_mcp.core.sandbox import execute_script, script_builtins

    target = str(uuid4())
    other = str(uuid4())
    models = [
        SimpleNamespace(guid="{" + target.upper() + "}"),
        SimpleNamespace(guid=other),
    ]
    calls = []

    def close_model(*, save_changes):
        calls.append(("close", save_changes))
        if not still_open:
            models.pop(0)

    def get_model_list():
        calls.append(("read", None))
        return SimpleNamespace(model_info=models)

    app = SimpleNamespace(close_model=close_model, get_model_list=get_model_list)
    script = driver._close_model_script(target)
    namespace = {"app": app, "__builtins__": script_builtins(lambda *_: None)}
    if still_open:
        with pytest.raises(AssertionError):
            execute_script(script, namespace)
    else:
        execute_script(script, namespace)
    assert calls == [("close", False), ("read", None)]
    assert models[-1].guid == other


async def test_repeated_real_task_cancel_reuses_and_settles_one_blocked_input_thread(
    monkeypatch,
):
    import threading

    release = threading.Event()
    entered = threading.Event()
    counter_lock = threading.Lock()
    started = 0
    finished = 0
    safe = False

    def blocking_input(_message):
        nonlocal started, finished
        with counter_lock:
            started += 1
        entered.set()
        release.wait()
        with counter_lock:
            finished += 1
        return ""

    monkeypatch.setattr("builtins.input", blocking_input)

    async def check():
        return safe

    hold = asyncio.create_task(_hold_until_safe(check, notice=lambda _: None))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        for _ in range(3):
            hold.cancel()
            await asyncio.sleep(0.02)
        with counter_lock:
            assert started == 1, "cancellation must reuse the same pending console read"
        safe = True
        hold.cancel()
        await asyncio.sleep(0.02)
        assert not hold.done(), (
            "even safe exit must settle the retained input thread first"
        )
        release.set()
        await asyncio.wait_for(hold, 2)
        with counter_lock:
            assert started == finished == 1
    finally:
        safe = True
        release.set()
        if not hold.done():
            await asyncio.wait_for(hold, 2)
