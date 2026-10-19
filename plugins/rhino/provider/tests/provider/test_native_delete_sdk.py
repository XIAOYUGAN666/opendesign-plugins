"""Opt-in real Rhino SDK/compiled Bridge receipts checked by their Provider contract."""

from __future__ import annotations

import json
import importlib
import os
from pathlib import Path
import subprocess

import pytest

from rhino_mcp.validation import validate_response


@pytest.fixture(scope="module")
def native_delete_receipts(tmp_path_factory):
    sdk = os.environ.get("RHINO_TEST_SDK_SYSTEM_DIR")
    native = os.environ.get("RHINO_TEST_NATIVE_NET48_DIR")
    if not sdk or not native:
        pytest.skip("requires explicit exact Rhino SDK and compiled net48 Bridge paths")
    sdk_root, native_root = Path(sdk), Path(native)
    assert (sdk_root / "RhinoCommon.dll").is_file()
    assert (native_root / "RhinoMcpBridge.rhp").is_file()
    compiler = (
        Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    assert compiler.is_file()
    source = Path(__file__).parents[1] / "fixtures/native_delete_sdk.cs"
    output = tmp_path_factory.mktemp("native-delete-sdk") / "DeleteSdkProbe.exe"
    built = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            f"/out:{output}",
            f"/reference:{sdk_root / 'RhinoCommon.dll'}",
            "/reference:System.Web.Extensions.dll",
            str(source),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    probe = subprocess.run(
        [str(output), str(sdk_root), str(native_root)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    lines = [
        line.removeprefix("RESULT_JSON=")
        for line in probe.stdout.splitlines()
        if line.startswith("RESULT_JSON=")
    ]
    assert len(lines) == 1
    report = json.loads(lines[0])
    assert report["softwareBuild"] == "8.26.25349.19001"
    return {case["kind"]: case for case in report["cases"]}


@pytest.mark.parametrize("kind", ["object", "all", "layer"])
def test_real_native_delete_receipt_matches_locked_contract_and_postconditions(
    native_delete_receipts,
    kind,
):
    case = native_delete_receipts[kind]
    validate_response(
        "delete_layer" if kind == "layer" else "delete_object",
        case["result"],
        raise_on_error=True,
    )
    assert case["result"]["deleted"] is True
    if kind == "all":
        assert case["objectCount"] == 0
        assert case["result"]["count"] == 2
        assert case["result"]["scope"] == "all"
    else:
        assert case["targetMissing"]
        assert case["result"]["id"] == case["targetId"]
        if kind == "object":
            assert case["anchorPreserved"] and case["objectCount"] == 1
        else:
            assert case["defaultPreserved"]
            assert case["currentLayerId"] == case["defaultLayerId"]


def test_layer_tool_formats_actual_sdk_receipt_without_missing_message(
    native_delete_receipts, monkeypatch
):
    case = native_delete_receipts["layer"]
    module = importlib.import_module("rhino_mcp.tools.delete_layer")
    calls = []

    class ObservedReceipt:
        def send_command(self, command, parameters):
            calls.append((command, parameters))
            return case["result"]

    monkeypatch.setattr(module, "get_rhino_connection", lambda _ctx: ObservedReceipt())
    assert (
        module.delete_layer(None, guid=case["targetId"])
        == "Deleted layer: SDK_DELETE_LAYER"
    )
    assert calls == [("delete_layer", {"guid": case["targetId"]})]
