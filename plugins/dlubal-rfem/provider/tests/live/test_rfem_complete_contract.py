"""Opt-in real RFEM contract gate for the self-contained release artifact.

The Dlubal credential is accepted only from an interactive prompt and is sent to
the Provider through its anonymous startup pipe.  The sanitized result never
contains the credential, bearer, canonical paths, or model contents.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import secrets
import socket
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

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


async def _terminal(client: Client, execution_id: str) -> dict[str, Any]:
    for _ in range(120):
        status = await client.call_tool(
            "get_rfem_execution", {"execution_id": execution_id}, raise_on_error=False
        )
        body = _structured(status)
        if body.get("state") != "running":
            return body
        await asyncio.sleep(1)
    raise AssertionError("RFEM execution did not reach a terminal state")


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
    release, port, token, connector, credential, data_dir, safe_identity
):
    from dlubal_rfem_mcp.core.config import StartupEnvelopeV2

    envelope = {
        "schemaVersion": 2,
        "mode": "execution",
        "connectorInstanceId": connector,
        "generation": 1,
        "bind": {"host": "127.0.0.1", "port": port, "path": "/mcp"},
        "bearerToken": token,
        "configuration": {"dlubal-api-key": credential},
        "data": {"stateDirectory": str(data_dir)},
        "software": {"safeIdentity": safe_identity, "build": EXPECTED_BUILD},
        "providerBuildId": release["providerBuildId"],
    }
    StartupEnvelopeV2.model_validate(envelope)
    return envelope


async def _drain_lifecycle(reader, envelope, schema, closing):
    from jsonschema import validators
    from referencing import Registry

    def deny(_uri):
        raise ValueError("externalSchemaReference")

    validator = validators.validator_for(schema)(
        schema, registry=Registry(retrieve=deny)
    )
    sequence = 0
    while line := await reader.readline():
        if len(line) > 4096:
            raise ValueError("invalidLifecycleFrame")
        frame = json.loads(line)
        validator.validate(frame)
        if (
            frame.get("kind") != "status"
            or frame.get("connectorInstanceId") != envelope["connectorInstanceId"]
            or frame.get("generation") != envelope["generation"]
            or type(frame.get("sequence")) is not int
            or frame["sequence"] != sequence + 1
        ):
            raise ValueError("invalidLifecycleIdentity")
        if frame["state"] == "idle" and frame["inFlight"] != 0:
            raise ValueError("invalidLifecycleState")
        sequence = frame["sequence"]
    if not closing.is_set():
        raise ValueError("lifecycleStreamClosed")
    return sequence


async def run_contract(artifact_root: Path, credential: str) -> dict[str, Any]:
    artifact_root = artifact_root.resolve(strict=True)
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
    cleanup_failed = False
    provider: asyncio.subprocess.Process | None = None
    lifecycle_task = None
    closing = asyncio.Event()

    with tempfile.TemporaryDirectory(prefix="dlubal-rfem-live-") as data_dir:
        database = str(Path(data_dir) / "executions.sqlite3")
        envelope = _startup_envelope(
            release, port, token, connector, credential, data_dir, "si_" + uuid4().hex
        )
        from jsonschema import validate

        startup_schema = json.loads(
            (artifact_root / "startup-envelope.schema.json").read_text("utf-8")
        )
        validate(envelope, startup_schema)
        try:
            provider = await asyncio.create_subprocess_exec(
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
            )
            assert provider.stdin and provider.stdout
            provider.stdin.write(_canonical(envelope))
            await provider.stdin.drain()
            provider.stdin.close()
            ready = json.loads(await asyncio.wait_for(provider.stdout.readline(), 30))
            assert ready == {
                "schemaVersion": 2,
                "kind": "ready",
                "mode": "execution",
                "connectorInstanceId": connector,
                "generation": 1,
                "providerBuildId": release["providerBuildId"],
                "port": port,
            }
            lifecycle_schema = json.loads(
                (artifact_root / "lifecycle.schema.json").read_text("utf-8")
            )
            validate(ready, lifecycle_schema)
            lifecycle_task = asyncio.create_task(
                _drain_lifecycle(provider.stdout, envelope, lifecycle_schema, closing)
            )

            transport = StreamableHttpTransport(
                f"http://127.0.0.1:{port}/mcp", auth=token
            )
            async with Client(transport) as client:
                discovered = [
                    tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for tool in await client.list_tools()
                ]
                assert {tool["name"] for tool in discovered} == set(EXPECTED_TOOLS)
                assert (
                    sorted(discovered, key=lambda tool: tool["name"])
                    == preview["tools"]
                )

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

                write = await client.call_tool(
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
                assert write_body["state"] == "succeeded", write_body
                assert model_guid

                calculate = await client.call_tool(
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
                assert calculate_body["state"] == "succeeded", calculate_body

                script = await client.call_tool(
                    "run_rfem_script",
                    {
                        "code": f'print("{marker}")',
                        "model_guid": model_guid,
                        "execution_id": execution_ids["script"],
                        "context_id": context_id,
                    },
                    raise_on_error=False,
                )
                script_body = _structured(script)
                if script_body.get("state") == "running":
                    script_body = await _terminal(client, execution_ids["script"])
                assert script_body["state"] == "succeeded", script_body
                assert marker in script_body.get("output", "")

                journal = await client.call_tool(
                    "get_rfem_execution", {"execution_id": execution_ids["script"]}
                )
                journal_body = _structured(journal)
                assert journal_body["state"] == "succeeded"
                assert journal_body["model_guid"] == model_guid

                results = await client.call_tool(
                    "read_rfem_results",
                    {
                        "result_type": "calculation_status",
                        "model_guid": model_guid,
                        "context_id": context_id,
                    },
                    raise_on_error=False,
                )
                results_body = _structured(results)
                assert results_body["status"] == "ok", results_body
                assert results_body["has_results"] is True
                assert results_body["source"]["model_guid"] == model_guid
                assert len(results_body["source"]["state_fingerprint"]["sha256"]) == 64
                assert len(results_body["result_hash"]) == 64

                cleanup = await client.call_tool(
                    "run_rfem_script",
                    {
                        "code": "app.close_model(save_changes=False)\nprint('DISPOSABLE_MODEL_CLOSED')",
                        "model_guid": model_guid,
                        "execution_id": execution_ids["cleanup"],
                        "context_id": context_id,
                    },
                    raise_on_error=False,
                )
                cleanup_body = _structured(cleanup)
                if cleanup_body.get("state") == "running":
                    cleanup_body = await _terminal(client, execution_ids["cleanup"])
                assert cleanup_body["state"] == "succeeded", cleanup_body
                model_guid = None

            assert Path(database).is_file()
            return {
                "artifactSha256": hashlib.sha256(
                    next(artifact_root.parent.glob("dlubal-rfem-*.zip")).read_bytes()
                ).hexdigest(),
                "providerBuildId": release["providerBuildId"],
                "softwareBuild": EXPECTED_BUILD,
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
            if (
                model_guid is not None
                and provider is not None
                and provider.returncode is None
            ):
                try:
                    cleanup_transport = StreamableHttpTransport(
                        f"http://127.0.0.1:{port}/mcp", auth=token
                    )
                    async with Client(cleanup_transport) as cleanup_client:
                        cleanup = await cleanup_client.call_tool(
                            "run_rfem_script",
                            {
                                "code": "app.close_model(save_changes=False)\nprint('DISPOSABLE_MODEL_CLOSED')",
                                "model_guid": model_guid,
                                "execution_id": str(uuid4()),
                                "context_id": context_id,
                            },
                            raise_on_error=False,
                        )
                        cleanup_body = _structured(cleanup)
                        if cleanup_body.get("state") == "running":
                            cleanup_body = await _terminal(
                                cleanup_client, cleanup_body["execution_id"]
                            )
                        if cleanup_body.get("state") == "succeeded":
                            model_guid = None
                except Exception:  # noqa: BLE001 - residue assertion below owns cleanup failure
                    cleanup_failed = True
            if provider is not None:
                closing.set()
                if provider.returncode is None:
                    provider.terminate()
                try:
                    await asyncio.wait_for(provider.wait(), 10)
                except TimeoutError:
                    provider.kill()
                    await provider.wait()
                if lifecycle_task is not None:
                    await lifecycle_task
                stderr = await provider.stderr.read() if provider.stderr else b""
                assert credential.encode() not in stderr
                assert token.encode() not in stderr
                assert database.encode() not in stderr
            if model_guid is not None:
                raise AssertionError(
                    "Disposable RFEM model may still be open; inspect the model named "
                    "with the OPENDESIGN_RFEM_LIVE prefix and close it without saving "
                    f"(automatic cleanup failed: {cleanup_failed})."
                )


@pytest.mark.skipif(
    os.environ.get("DLUBAL_RFEM_LIVE") != "1",
    reason="real RFEM gate requires explicit run-live-contract.ps1 invocation",
)
def test_complete_self_contained_rfem_contract() -> None:
    root = os.environ.get("DLUBAL_RFEM_ARTIFACT_ROOT")
    if not root:
        pytest.fail("DLUBAL_RFEM_ARTIFACT_ROOT is required for the live gate")
    credential = getpass.getpass("Dlubal API key: ")
    try:
        evidence = asyncio.run(run_contract(Path(root), credential))
    finally:
        credential = ""
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True, type=Path)
    args = parser.parse_args()
    credential = getpass.getpass("Dlubal API key: ")
    try:
        evidence = asyncio.run(run_contract(args.artifact_root, credential))
    finally:
        credential = ""
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
