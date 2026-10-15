"""Real Provider BridgeClient interoperability with the native test host."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from rhino_mcp.bridge import BridgeClient, BridgeRejected

from tests.fixtures.bridge_contract import (
    CONTRACT_DIGEST,
    TOKEN,
    bridge_config,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
HOST_DLL = Path(
    os.environ.get(
        "RHINO_BRIDGE_TEST_HOST_DLL",
        PLUGIN_ROOT
        / "native"
        / "tests"
        / "BridgeTestHost"
        / "bin"
        / "Release"
        / "net8.0"
        / "BridgeTestHost.dll",
    )
).resolve()
BRIDGE_DLL = Path(
    os.environ.get(
        "RHINO_BRIDGE_PLUGIN_DLL",
        PLUGIN_ROOT / "native" / "bin" / "Release" / "net8.0" / "RhinoMcpBridge.rhp",
    )
).resolve()


def _dotnet_executable() -> str:
    configured = os.environ.get("RHINO_DOTNET_EXE")
    if configured:
        return configured
    executable = shutil.which("dotnet")
    if executable is None:
        raise AssertionError(
            "Set RHINO_DOTNET_EXE to the .NET host used to build the native test host."
        )
    return executable


def _read_line_with_timeout(process: subprocess.Popen[str], timeout: float) -> str:
    assert process.stdout is not None
    lines: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

    def read_line() -> None:
        try:
            lines.put(process.stdout.readline())
        except BaseException as exc:  # pragma: no cover - only forwards thread errors
            lines.put(exc)

    threading.Thread(target=read_line, daemon=True).start()
    try:
        result = lines.get(timeout=timeout)
    except queue.Empty as exc:
        raise AssertionError(
            "Native Bridge test host did not report readiness."
        ) from exc
    if isinstance(result, BaseException):
        raise result
    return result


def test_provider_bridge_client_interoperates_with_native_server_process() -> None:
    assert HOST_DLL.is_file(), f"Native test host was not built: {HOST_DLL}"
    assert BRIDGE_DLL.is_file(), f"Native Bridge was not built: {BRIDGE_DLL}"

    process = subprocess.Popen(
        [_dotnet_executable(), str(HOST_DLL), str(BRIDGE_DLL)],
        cwd=PLUGIN_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    bridge: BridgeClient | None = None
    try:
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "generation": 1,
                    "bridgeToken": TOKEN,
                    "delayCommand": "",
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        process.stdin.flush()
        ready_line = _read_line_with_timeout(process, timeout=10.0)
        assert ready_line, "Native Bridge test host exited before readiness."
        ready = json.loads(ready_line)
        assert set(ready) == {"kind", "host", "port", "providerBuildId"}
        assert ready["kind"] == "bridgeReady"
        assert ready["host"] == "127.0.0.1"
        assert 0 < ready["port"] < 65536
        assert isinstance(ready["providerBuildId"], str)
        assert ready["providerBuildId"].startswith("sha256:")

        for generation, token in (
            (1, "wrong-native-token-0123456789abcdef"),
            (2, TOKEN),
        ):
            rejected = BridgeClient(
                bridge_config(
                    (ready["host"], ready["port"]),
                    generation=generation,
                    token=token,
                    provider_build_id=ready["providerBuildId"],
                    contract_digest=CONTRACT_DIGEST,
                )
            )
            try:
                with pytest.raises(BridgeRejected):
                    rejected.call("describe_capabilities", {})
                assert rejected.lifecycle_snapshot() == ("idle", 0)
            finally:
                rejected.disconnect()

        bridge = BridgeClient(
            bridge_config(
                (ready["host"], ready["port"]),
                token=TOKEN,
                provider_build_id=ready["providerBuildId"],
                contract_digest=CONTRACT_DIGEST,
            )
        )
        result = bridge.call("describe_capabilities", {})
        assert result["version"] == "0.1.2"
        assert result["command_count"] == 3
        assert result["perception"]["envelope_flags"] == []
    finally:
        if bridge is not None:
            bridge.disconnect()
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5.0)
        if process.stderr is not None:
            process.stderr.close()
