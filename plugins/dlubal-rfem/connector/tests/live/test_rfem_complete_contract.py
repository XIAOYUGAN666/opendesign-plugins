"""Opt-in real RFEM contract gate for the self-contained release artifact.

The Dlubal credential is accepted only from an interactive prompt and is sent to
the Connector through its anonymous startup pipe.  The sanitized result never
contains the credential, bearer, canonical paths, or model contents.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import math
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

EXPECTED_BUILD = "6.14.0013.249"
EXPECTED_TOOLS = [
    "health_check",
    "get_rfem_application_info",
    "inspect_rfem_api",
    "write_rfem_model",
    "calculate_rfem_model",
    "run_rfem_script",
    "get_rfem_execution",
    "read_rfem_results",
]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _text(result: Any) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


def _structured(result: Any) -> dict[str, Any]:
    value = result.structured_content
    if isinstance(value, dict):
        return value
    raise AssertionError("tool did not return structured content")


async def _terminal(client: Any, execution_id: str) -> dict[str, Any]:
    for _ in range(120):
        status = await client.call_tool(
            "get_rfem_execution", {"execution_id": execution_id}, raise_on_error=False
        )
        body = _structured(status)
        if body.get("state") in {"succeeded", "failed"}:
            return body
        if body.get("state") != "running":
            raise RuntimeError("unsettled RFEM execution; retain the same operation ID")
        await asyncio.sleep(1)
    raise AssertionError("RFEM execution did not reach a terminal state")


@dataclass
class LifecycleObservation:
    state: str = "unverifiable"
    in_flight: int = -1
    observed_at: float = 0.0
    failed: bool = False
    sequence: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def idle(self) -> bool:
        return (
            not self.failed
            and self.state == "idle"
            and self.in_flight == 0
            and 0 <= time.monotonic() - self.observed_at < 5
        )


@dataclass
class DriverJournal:
    directory: Path
    executions: dict[str, dict[str, Any]] = field(default_factory=dict)
    observation: LifecycleObservation | None = None
    lifecycle_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None

    def save(self) -> None:
        # Exact IDs and bounded state only: no input code, model contents or secrets.
        path = self.directory / "driver-journal.json"
        pending = path.with_suffix(".tmp")
        pending.write_bytes(_canonical({"executions": self.executions}))
        pending.replace(path)

    async def dispatch(
        self, client: Any, name: str, arguments: dict[str, Any], **kwargs
    ):
        if (
            self.observation is None
            or self.lifecycle_task is None
            or not await _safe_to_shutdown(
                client,
                self,
                self.observation,
                self.lifecycle_task,
                require_new_sample=True,
            )
        ):
            raise RuntimeError(
                "unsettled lifecycle or prior execution; engineering dispatch refused"
            )
        execution_id = arguments["execution_id"]
        if execution_id in self.executions:
            raise RuntimeError("driver refuses engineering replay")
        self.executions[execution_id] = {
            "tool": name,
            "state": "dispatched",
            "dispatchSequence": self.observation.sequence,
        }
        self.save()  # Before the first dispatch await, including lost responses.
        result = await client.call_tool(name, arguments, **kwargs)
        return result


def _readers_healthy(journal, observation, lifecycle_task) -> bool:
    return (
        not lifecycle_task.done()
        and not observation.failed
        and (journal.stderr_task is None or not journal.stderr_task.done())
    )


async def _safe_to_shutdown(
    client, journal, observation, lifecycle_task, *, require_new_sample: bool = False
) -> bool:
    if not _readers_healthy(journal, observation, lifecycle_task):
        return False
    settled = True
    for execution_id, entry in journal.executions.items():
        try:
            response = await client.call_tool(
                "get_rfem_execution",
                {"execution_id": execution_id},
                raise_on_error=False,
            )
            body = _structured(response)
            state = body.get("state")
            # A missing/rejected ID after an uncertain dispatch is never evidence
            # that an engineering action did not happen.
            if body.get("execution_id") != execution_id or state not in {
                "succeeded",
                "failed",
            }:
                settled = False
            entry["state"] = (
                state
                if state in {"succeeded", "failed", "running", "unknown"}
                else "unsettled"
            )
            if isinstance(body.get("model_guid"), str):
                entry["model_guid"] = body["model_guid"]
        except Exception:  # noqa: BLE001 - uncertain local query must preserve the owner
            entry["state"] = "query_failed"
            settled = False
    journal.save()
    if not settled or not _readers_healthy(journal, observation, lifecycle_task):
        return False
    if not journal.executions and not require_new_sample:
        return observation.idle() and _readers_healthy(
            journal, observation, lifecycle_task
        )
    if not journal.executions and observation.state == "unknown":
        return False
    # A pre-dispatch idle (even fresh) cannot prove post-operation settlement.
    # Require a newly issued authenticated heartbeat AFTER all terminal local
    # journal responses, or the initial read-only SDK checks before first write.
    # This waits on the owned stream, never on vendor polling or a replay.
    after_query = observation.sequence
    observation.changed.clear()
    try:
        async with asyncio.timeout(5):
            while observation.sequence <= after_query:
                await observation.changed.wait()
                observation.changed.clear()
    except TimeoutError:
        return False
    return observation.idle() and _readers_healthy(journal, observation, lifecycle_task)


async def _startup_exited_without_dispatch(connector, journal) -> bool:
    if journal.executions:
        return False
    # Await the owned child's real exit notification; a cancelled wait never
    # kills it. Startup failure without ANY engineering dispatch needs no MCP.
    try:
        await asyncio.wait_for(connector.wait(), 0.2)
    except TimeoutError:
        return False
    return connector.returncode is not None


async def _operator_prompt() -> None:
    await asyncio.to_thread(
        input,
        "After inspecting RFEM and resolving the SAME IDs with the existing execution CLI, press Enter to recheck local journal/lifecycle only: ",
    )


async def _hold_until_safe(check, prompt=_operator_prompt, notice=print) -> None:
    # Shield and reuse ONE console read across cancellation. Cancelling to_thread
    # does not stop input(): abandoning its task would strand an executor worker.
    async def read_prompt() -> bool:
        try:
            await prompt()
        except (EOFError, KeyboardInterrupt):
            return False
        return True

    pending_prompt: asyncio.Task[bool] | None = None
    while True:
        try:
            if await check():
                if pending_prompt is not None:
                    await asyncio.shield(pending_prompt)
                    pending_prompt = None
                return
            notice(
                "UNSETTLED engineering state: Connector, readers and evidence are retained. No cleanup/replay/automatic resolution. Do not close this driver."
            )
            if pending_prompt is None:
                pending_prompt = asyncio.create_task(read_prompt())
            available = await asyncio.shield(pending_prompt)
            pending_prompt = None
            if not available:
                notice(
                    "Input unavailable/interrupted; owner retained. Rechecking only the LOCAL journal/lifecycle after 30 seconds; no vendor requests or automatic resolution."
                )
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            if pending_prompt is not None and pending_prompt.cancelled():
                pending_prompt = None
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            notice(
                "Cancellation refused while engineering state is unresolved; owner retained."
            )


async def _drain_stderr(
    reader, captured: bytearray, forbidden: tuple[bytes, ...] = ()
) -> None:
    while block := await reader.read(4096):
        captured.extend(block)
        if any(value and value in captured for value in forbidden):
            raise RuntimeError("private diagnostic boundary violation")
        del captured[:-65536]  # Bound private in-memory diagnostics; never print it.


def _readback_script(marker: str, model_guid: str) -> str:
    # Only injected app/rfem/math objects; BoundApplication injects exact ModelId
    # into every get_object call, preventing active-model fallback.
    return f'''info = app.get_application_info()
assert ".".join(info.full_version.split(".")[:4]) == "{EXPECTED_BUILD}"
assert app.get_active_model().guid.strip("{{}}").lower() == "{model_guid.strip("{}").lower()}"
n1 = app.get_object(rfem.structure_core.Node(no=1))
n2 = app.get_object(rfem.structure_core.Node(no=2))
assert (n1.coordinate_1, n1.coordinate_2, n1.coordinate_3) == (0.0, 0.0, 0.0)
assert (n2.coordinate_1, n2.coordinate_2, n2.coordinate_3) == (6.0, 0.0, 0.0)
line = app.get_object(rfem.structure_core.Line(no=1))
assert list(line.definition_nodes) == [1, 2]
member = app.get_object(rfem.structure_core.Member(no=1))
assert member.line == 1 and member.cross_section_start == 1
material = app.get_object(rfem.structure_core.Material(no=1))
section = app.get_object(rfem.structure_core.CrossSection(no=1))
assert "S235" in material.name and "IPE240" in section.name.replace(" ", "")
assert section.material == 1
support = app.get_object(rfem.types_for_nodes.NodalSupport(no=901))
assert list(support.nodes) == [1]
for value in [support.spring_x, support.spring_y, support.spring_z, support.rotational_restraint_x, support.rotational_restraint_y, support.rotational_restraint_z]:
    assert math.isinf(value) and value > 0
settings = app.get_object(rfem.loading.StaticAnalysisSettings(no=901))
assert settings.analysis_type == rfem.loading.StaticAnalysisSettings.ANALYSIS_TYPE_GEOMETRICALLY_LINEAR
case = app.get_object(rfem.loading.LoadCase(no=901))
assert case.name == "LC901" and case.self_weight_active is False and case.static_analysis_settings == 901
load = app.get_object(rfem.loads.MemberLoad(no=901, load_case=901))
assert list(load.members) == [1] and load.load_case == 901 and load.magnitude == -10000.0
assert load.load_type == rfem.loads.MemberLoad.LOAD_TYPE_FORCE
assert load.load_distribution == rfem.loads.MemberLoad.LOAD_DISTRIBUTION_UNIFORM
assert load.load_direction == rfem.loads.MemberLoad.LOAD_DIRECTION_GLOBAL_Z_OR_USER_DEFINED_W_TRUE_LENGTH
print("{marker}")
print("EXACT_BUILD_AND_MODEL_READBACK_OK")
'''


def _close_model_script(model_guid: str) -> str:
    target = str(UUID(model_guid.strip("{}")))
    return f'''app.close_model(save_changes=False)
for model in app.get_model_list().model_info:
    assert model.guid.strip("{{}}").lower() != "{target}"
print("DISPOSABLE_MODEL_CLOSED")
'''


def _assert_deformations(body: dict[str, Any], model_guid: str) -> float:
    assert body["status"] == "ok"
    assert body["has_results"] is True
    assert body["source"]["model_guid"] == model_guid
    assert body["units"]["u_z"] == "m"
    rows = body["rows"]
    assert len(rows) == 1 and all(
        row["node_no"] == 2 and row["loading"] == "LC901" for row in rows
    )
    uz = rows[0]["u_z"]
    assert isinstance(uz, (int, float)) and not isinstance(uz, bool)
    assert math.isfinite(uz) and uz < -1e-12
    return float(uz)


def _stream_digest(stream: Any) -> bytes:
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.digest()


def _artifact_process_root(artifact_root: Path) -> Path:
    """Keep the verified directory, using the same Win32 launch seam as Host.

    Embedded Python normalizes extended path prefixes during import. Long
    installed roots can therefore hide real dependency files from importlib.
    A verified short alias avoids copying or changing any signed artifact byte.
    """
    if not artifact_root.is_dir():
        raise ValueError("artifactDirectoryUnavailable")
    if os.name != "nt":
        return artifact_root
    import ctypes
    from ctypes import wintypes

    short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    short_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = short_path(str(artifact_root), buffer, len(buffer))
    if not size or size >= len(buffer):
        raise ValueError("artifactProcessPathUnavailable")
    result = Path(buffer.value)
    if not result.samefile(artifact_root):
        raise ValueError("artifactProcessPathMismatch")
    return result


def _bound_archive(artifact_root: Path, archive: Path) -> str:
    archive = archive.resolve(strict=True)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with zipfile.ZipFile(archive) as package:
        entries = [item for item in package.infolist() if not item.is_dir()]
        names = [item.filename for item in entries]
        if len(set(names)) != len(names):
            raise ValueError("duplicateArtifactPath")
        if any(
            "\\" in name or name.startswith("/") or ".." in Path(name).parts
            for name in names
        ):
            raise ValueError("unsafeArtifactPath")
        actual = {
            path.relative_to(artifact_root).as_posix()
            for path in artifact_root.rglob("*")
            if path.is_file()
        }
        if actual != set(names):
            raise ValueError("artifactExtractionMismatch")
        for entry in entries:
            with (
                package.open(entry) as packed,
                (artifact_root / entry.filename).open("rb") as installed,
            ):
                if _stream_digest(packed) != _stream_digest(installed):
                    raise ValueError("artifactExtractionMismatch")
    return digest


def _clean_environment() -> dict[str, str]:
    return {
        name: os.environ[name]
        for name in (
            "SystemRoot",
            "WINDIR",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
        )
        if name in os.environ
    }


def _startup_envelope(
    release, port, token, connector_instance_id, credential, data_dir, safe_identity
):
    binding = {
        "packageId": "dlubal-rfem",
        "releaseDigest": release["releaseDigest"],
        "connectorInstanceId": connector_instance_id,
        "generation": 1,
        "configurationRevision": 1,
        "secretRevision": 1,
    }
    return {
        "schemaVersion": 3,
        "binding": binding,
        "mode": "runtime",
        "bind": {"host": "127.0.0.1", "port": port, "path": "/mcp"},
        "bearerToken": token,
        "connectorBuildId": release["connectorBuildId"],
        "configuration": {},
        "secrets": [{"fieldId": "dlubal-api-key", "value": credential}],
        "dataDirectory": str(data_dir),
        "nativePeer": None,
    }


async def _drain_lifecycle(reader, envelope, schema, closing, observation):
    from jsonschema import validators
    from referencing import Registry

    def deny(_uri):
        raise ValueError("externalSchemaReference")

    validator = validators.validator_for(schema)(
        schema, registry=cast(Any, Registry)(retrieve=deny)
    )
    sequence = 0
    while line := await reader.readline():
        if len(line) > 4096:
            raise ValueError("invalidLifecycleFrame")
        frame = json.loads(line)
        validator.validate(frame)
        if (
            frame.get("kind") != "status"
            or frame.get("connectorInstanceId") != envelope["binding"]["connectorInstanceId"]
            or frame.get("generation") != envelope["binding"]["generation"]
            or type(frame.get("sequence")) is not int
            or frame["sequence"] != sequence + 1
        ):
            raise ValueError("invalidLifecycleIdentity")
        if frame["state"] == "idle" and frame["inFlight"] != 0:
            raise ValueError("invalidLifecycleState")
        sequence = frame["sequence"]
        observation.state = frame["state"]
        observation.in_flight = frame["inFlight"]
        observation.observed_at = time.monotonic()
        observation.sequence = sequence
        observation.changed.set()
    if not closing.is_set():
        raise ValueError("lifecycleStreamClosed")
    return sequence


async def run_contract(
    artifact_root: Path, credential: str, artifact_archive: Path
) -> dict[str, Any]:
    artifact_root = _artifact_process_root(artifact_root.resolve(strict=True))
    archive_digest = _bound_archive(artifact_root, artifact_archive)
    release = json.loads((artifact_root / "release.json").read_text("utf-8"))
    preview = json.loads((artifact_root / "tool-preview.json").read_text("utf-8"))
    assert release["software"]["supportedBuilds"] == [EXPECTED_BUILD]
    assert release["adapter"] == {"distribution": "dlubal.api", "version": "2.14.13"}
    assert (
        release["toolCatalogDigest"]
        == "sha256:" + hashlib.sha256(_canonical(preview)).hexdigest()
    )

    port = _free_port()
    token = secrets.token_urlsafe(48)
    connector = "ci_" + uuid4().hex
    context_id = "rfem-live-" + uuid4().hex
    marker = "OPENDESIGN_RFEM_LIVE_" + uuid4().hex
    execution_ids = {
        name: str(uuid4()) for name in ("write", "calculate", "script", "cleanup")
    }
    model_guid: str | None = None
    connector: asyncio.subprocess.Process | None = None
    lifecycle_task = None
    closing = asyncio.Event()
    observation = LifecycleObservation()
    stderr_task = None
    stderr_bytes = bytearray()
    success = False

    data_dir = tempfile.mkdtemp(prefix="dlubal-rfem-live-")
    journal = DriverJournal(Path(data_dir))
    database = str(Path(data_dir) / "executions.sqlite3")
    release["releaseDigest"] = archive_digest
    envelope = _startup_envelope(
        release, port, token, connector, credential, data_dir, "si_" + uuid4().hex
    )
    from jsonschema import validate

    startup_schema = json.loads(
        (artifact_root / "startup-envelope.schema.json").read_text("utf-8")
    )
    validate(envelope, startup_schema)
    try:
        connector = await asyncio.create_subprocess_exec(
            str(artifact_root / "runtime/python/python.exe"),
            "-I",
            "-s",
            "-B",
            "-m",
            "dlubal_rfem_mcp.managed_bootstrap",
            cwd=artifact_root,
            env=_clean_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        assert connector.stdin and connector.stdout and connector.stderr
        stderr_task = asyncio.create_task(
            _drain_stderr(
                connector.stderr,
                stderr_bytes,
                (credential.encode(), token.encode(), database.encode()),
            )
        )
        journal.stderr_task = stderr_task
        connector.stdin.write(_canonical(envelope) + b"\n")
        await connector.stdin.drain()
        ready = json.loads(await asyncio.wait_for(connector.stdout.readline(), 30))
        assert ready == {
            "schemaVersion": 3,
            "kind": "ready",
            "binding": envelope["binding"],
            "connectorBuildId": release["connectorBuildId"],
            "port": port,
        }
        lifecycle_schema = json.loads(
            (artifact_root / "execution-status.schema.json").read_text("utf-8")
        )
        validate(ready, lifecycle_schema)
        lifecycle_task = asyncio.create_task(
            _drain_lifecycle(
                connector.stdout, envelope, lifecycle_schema, closing, observation
            )
        )

        journal.observation = observation
        journal.lifecycle_task = lifecycle_task
        transport = StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp", auth=token)
        async with Client(transport) as client:
            discovered = [
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in await client.list_tools()
            ]
            assert {tool["name"] for tool in discovered} == set(EXPECTED_TOOLS)
            assert sorted(discovered, key=lambda tool: tool["name"]) == preview["tools"]

            health = await client.call_tool("health_check", {})
            health_body = json.loads(_text(health))
            assert health_body["products"]["rfem"]["running"] is True
            assert set(health_body["tools"]) == set(EXPECTED_TOOLS)

            info = await client.call_tool("get_rfem_application_info", {})
            info_body = _structured(info)
            assert info_body["status"] == "ok"
            assert str(info_body["version"]).startswith("6.14")

            inspected = await client.call_tool(
                "inspect_rfem_api", {"query": "Material"}
            )
            assert "material_type" in _text(inspected)

            write = await journal.dispatch(
                client,
                "write_rfem_model",
                {
                    "model_name": marker,
                    "z_axis": "up",
                    "execution_id": execution_ids["write"],
                    "context_id": context_id,
                    "objects": [
                        {"kind": "Material", "no": 1, "fields": {"name": "S235"}},
                        {
                            "kind": "CrossSection",
                            "no": 1,
                            "fields": {"name": "IPE 240", "material": 1},
                        },
                        {
                            "kind": "Node",
                            "no": 1,
                            "fields": {
                                "coordinate_1": 0.0,
                                "coordinate_2": 0.0,
                                "coordinate_3": 0.0,
                            },
                        },
                        {
                            "kind": "Node",
                            "no": 2,
                            "fields": {
                                "coordinate_1": 6.0,
                                "coordinate_2": 0.0,
                                "coordinate_3": 0.0,
                            },
                        },
                        {
                            "kind": "Line",
                            "no": 1,
                            "fields": {"definition_nodes": [1, 2]},
                        },
                        {
                            "kind": "Member",
                            "no": 1,
                            "fields": {"line": 1, "cross_section_start": 1},
                        },
                        {
                            "kind": "NodalSupport",
                            "no": 901,
                            "fields": {
                                "nodes": [1],
                                "spring_x": "Infinity",
                                "spring_y": "Infinity",
                                "spring_z": "Infinity",
                                "rotational_restraint_x": "Infinity",
                                "rotational_restraint_y": "Infinity",
                                "rotational_restraint_z": "Infinity",
                            },
                        },
                        {
                            "kind": "StaticAnalysisSettings",
                            "no": 901,
                            "fields": {
                                "analysis_type": "ANALYSIS_TYPE_GEOMETRICALLY_LINEAR"
                            },
                        },
                        {
                            "kind": "LoadCase",
                            "no": 901,
                            "fields": {
                                "name": "LC901",
                                "static_analysis_settings": 901,
                                "self_weight_active": False,
                                "to_solve": True,
                            },
                        },
                        {
                            "kind": "MemberLoad",
                            "no": 901,
                            "fields": {
                                "load_type": "LOAD_TYPE_FORCE",
                                "members": [1],
                                "load_case": 901,
                                "load_distribution": "LOAD_DISTRIBUTION_UNIFORM",
                                "load_direction": "LOAD_DIRECTION_GLOBAL_Z_OR_USER_DEFINED_W_TRUE_LENGTH",
                                "magnitude": -10000.0,
                            },
                        },
                    ],
                },
                raise_on_error=False,
            )
            write_body = _structured(write)
            if write_body.get("state") == "running":
                write_body = await _terminal(client, execution_ids["write"])
            model_guid = write_body.get("model_guid")
            assert write_body["state"] == "succeeded"
            assert model_guid

            calculate = await journal.dispatch(
                client,
                "calculate_rfem_model",
                {
                    "model_guid": model_guid,
                    "execution_id": execution_ids["calculate"],
                    "context_id": context_id,
                },
                raise_on_error=False,
            )
            calculate_body = _structured(calculate)
            if calculate_body.get("state") == "running":
                calculate_body = await _terminal(client, execution_ids["calculate"])
            assert calculate_body["state"] == "succeeded"

            script = await journal.dispatch(
                client,
                "run_rfem_script",
                {
                    "code": _readback_script(marker, model_guid),
                    "model_guid": model_guid,
                    "execution_id": execution_ids["script"],
                    "context_id": context_id,
                },
                raise_on_error=False,
            )
            script_body = _structured(script)
            if script_body.get("state") == "running":
                script_body = await _terminal(client, execution_ids["script"])
            assert script_body["state"] == "succeeded"
            assert marker in script_body.get("output", "")

            for name in ("write", "calculate", "script"):
                status = await client.call_tool(
                    "get_rfem_execution", {"execution_id": execution_ids[name]}
                )
                body = _structured(status)
                assert body["execution_id"] == execution_ids[name]
                assert body["state"] == "succeeded"
                assert body["model_guid"] == model_guid

            results = await client.call_tool(
                "read_rfem_results",
                {
                    "result_type": "node_deformations",
                    "object_nos": [2],
                    "loadings": ["LC901"],
                    "model_guid": model_guid,
                    "context_id": context_id,
                },
                raise_on_error=False,
            )
            results_body = _structured(results)
            assert results_body["status"] == "ok"
            displacement = _assert_deformations(results_body, model_guid)
            assert results_body["source"]["model_guid"] == model_guid
            assert len(results_body["source"]["state_fingerprint"]["sha256"]) == 64
            assert len(results_body["result_hash"]) == 64

            if not await _safe_to_shutdown(
                client, journal, observation, lifecycle_task
            ):
                raise RuntimeError(
                    "unsettled engineering action; no cleanup dispatched"
                )
            cleanup = await journal.dispatch(
                client,
                "run_rfem_script",
                {
                    "code": _close_model_script(model_guid),
                    "model_guid": model_guid,
                    "execution_id": execution_ids["cleanup"],
                    "context_id": context_id,
                },
                raise_on_error=False,
            )
            cleanup_body = _structured(cleanup)
            if cleanup_body.get("state") == "running":
                cleanup_body = await _terminal(client, execution_ids["cleanup"])
            assert cleanup_body["state"] == "succeeded"
            model_guid = None

        assert Path(database).is_file()
        success = True
        return {
            "artifactSha256": archive_digest,
            "connectorBuildId": release["connectorBuildId"],
            "softwareBuild": EXPECTED_BUILD,
            "node2LC901UzMetres": displacement,
            "executions": journal.executions,
            "tools": EXPECTED_TOOLS,
            "toolCatalogDigest": release["toolCatalogDigest"],
            "states": {
                "write": "succeeded",
                "calculate": "succeeded",
                "script": "succeeded",
                "journal": "succeeded",
                "results": "ok",
                "cleanup": "succeeded",
            },
        }
    finally:
        if connector is not None:
            # Keep the local control transport and both process readers alive until
            # every dispatched ID and the authenticated lifecycle prove settlement.
            async def check():
                if lifecycle_task is None:
                    return False
                try:
                    async with Client(
                        StreamableHttpTransport(
                            f"http://127.0.0.1:{port}/mcp", auth=token
                        )
                    ) as local_client:
                        return await _safe_to_shutdown(
                            local_client, journal, observation, lifecycle_task
                        )
                except Exception:  # noqa: BLE001 - failed control query is not settlement
                    return False

            previous_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(
                signal.SIGINT,
                lambda *_: print(
                    "UNSETTLED driver: Ctrl-C does not release an engineering owner."
                ),
            )
            try:
                print("Local-only retained state directory:", data_dir)
                print("Exact dispatched execution IDs:", ", ".join(journal.executions))
                print(
                    "Operator recovery ONLY after inspecting partial changes and verifying RFEM idle: use the installed runtime/python/python.exe -I -s -B -m dlubal_rfem_mcp.rfem.execution --state-file",
                    database,
                    "--resolve SAME-EXACT-ID --confirmed-rfem-idle. The driver never executes this command.",
                )
                if not await _startup_exited_without_dispatch(connector, journal):
                    print(
                        "If the lifecycle reader has permanently failed, Enter cannot repair its proof. External manual inspection/recovery is required; this driver retains its owner."
                    )
                    await _hold_until_safe(check)
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
            closing.set()
            if connector.returncode is None:
                connector.terminate()
            await asyncio.wait_for(connector.wait(), 10)
            if lifecycle_task is not None:
                await lifecycle_task
            if stderr_task is not None:
                await stderr_task
            assert credential.encode() not in stderr_bytes
            assert token.encode() not in stderr_bytes
            assert database.encode() not in stderr_bytes
        if success and model_guid is None:
            shutil.rmtree(data_dir)
        elif model_guid is not None or journal.executions:
            # No fresh cleanup ID or automatic replay after any failed assertion.
            print(
                "Disposable test model remains open; inspect only the OPENDESIGN_RFEM_LIVE model and close it manually without saving."
            )


def _read_credential() -> str:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "A real interactive terminal is required for secure API-key input"
        )
    # Reject getpass's echoing fallback before it can read a credential.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        value = getpass.getpass("Dlubal API key: ")
    if not value:
        raise RuntimeError("An API key is required for the real RFEM gate")
    return value


def _run_interactive(
    artifact_root: Path, credential: str, archive: Path
) -> dict[str, Any]:
    # Install before asyncio.run, so its second Ctrl-C fallback cannot unwind an
    # unresolved owner. The child has its own process group and receives no Ctrl-C.
    signals = [signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    previous = {item: signal.getsignal(item) for item in signals}

    def retain(*_args):
        print(
            "Engineering driver owns a live process: interruption refused until exact execution settlement. Use the operator recovery instructions, not Ctrl-C."
        )

    for item in signals:
        signal.signal(item, retain)
    try:
        return asyncio.run(run_contract(artifact_root, credential, archive))
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


@pytest.mark.skipif(
    os.environ.get("DLUBAL_RFEM_LIVE") != "1",
    reason="real RFEM gate requires explicit run-live-contract.ps1 invocation",
)
def test_complete_self_contained_rfem_contract() -> None:
    root = os.environ.get("DLUBAL_RFEM_ARTIFACT_ROOT")
    if not root:
        pytest.fail("DLUBAL_RFEM_ARTIFACT_ROOT is required for the live gate")
    archive = os.environ.get("DLUBAL_RFEM_ARTIFACT_ARCHIVE")
    if not archive:
        pytest.fail("DLUBAL_RFEM_ARTIFACT_ARCHIVE is required")
    credential = _read_credential()
    try:
        evidence = _run_interactive(Path(root), credential, Path(archive))
    finally:
        credential = ""
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--artifact-archive", required=True, type=Path)
    args = parser.parse_args()
    credential = _read_credential()
    try:
        evidence = _run_interactive(
            args.artifact_root, credential, args.artifact_archive
        )
    finally:
        credential = ""
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
