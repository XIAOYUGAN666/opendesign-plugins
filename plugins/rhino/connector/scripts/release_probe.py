"""Black-box MCP discovery probe for the staged private Connector runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any


class ReleaseProbeError(RuntimeError):
    """A staged Connector failed the bounded release discovery probe."""


def _read_exact(connection: socket.socket, size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        chunk = connection.recv(size - len(payload))
        if not chunk:
            raise ConnectionError("Bridge peer closed during a framed message.")
        payload.extend(chunk)
    return bytes(payload)


def _read_frame(connection: socket.socket) -> dict[str, Any]:
    size = int.from_bytes(_read_exact(connection, 4), "big")
    if not 0 < size <= 64 * 1024:
        raise ValueError("Bridge authentication frame has an invalid length.")
    value = json.loads(_read_exact(connection, size).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Bridge frame must contain a JSON object.")
    return value


def _write_frame(connection: socket.socket, value: dict[str, Any]) -> None:
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    connection.sendall(len(payload).to_bytes(4, "big") + payload)


class _ProbeBridge:
    """One-use loopback Bridge that accepts only this probe's generation."""

    def __init__(self, connector_build_id: str, contract_digest: str) -> None:
        self.connector_build_id = connector_build_id
        self.contract_digest = contract_digest
        self.token = "B" * 40
        self.generation = 1
        self.authenticated = threading.Event()
        self.error: BaseException | None = None
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.1)
        self.endpoint = ("127.0.0.1", self.listener.getsockname()[1])
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stopping.set()
        self.listener.close()
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        while not self._stopping.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                with connection:
                    connection.settimeout(5)
                    auth = _read_frame(connection)
                    expected = {
                        "type": "authenticate",
                        "generation": self.generation,
                        "token": self.token,
                        "connectorBuildId": self.connector_build_id,
                        "contractDigest": self.contract_digest,
                    }
                    if auth != expected:
                        _write_frame(connection, {"type": "authenticationRejected"})
                        raise ReleaseProbeError(
                            "Staged Connector Bridge identity mismatched."
                        )
                    _write_frame(
                        connection,
                        {
                            "type": "authenticated",
                            "generation": self.generation,
                            "connectorBuildId": self.connector_build_id,
                            "contractDigest": self.contract_digest,
                        },
                    )
                    self.authenticated.set()
                    while not self._stopping.is_set():
                        try:
                            command = _read_frame(connection)
                        except (ConnectionError, OSError, TimeoutError):
                            return
                        if command.get("type") == "authenticate":
                            raise ReleaseProbeError(
                                "Connector repeated Bridge authentication."
                            )
                        _write_frame(
                            connection,
                            {
                                "status": "error",
                                "message": "Release probe does not execute Rhino commands.",
                            },
                        )
            except (ConnectionError, OSError, TimeoutError) as exc:
                if not self._stopping.is_set():
                    self.error = exc
                return
            except BaseException as exc:
                self.error = exc
                return


def _free_port(excluded: int) -> int:
    for _ in range(8):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        if port != excluded:
            return port
    raise ReleaseProbeError("Could not allocate a distinct loopback probe port.")


def _read_line_with_timeout(process: subprocess.Popen[str], timeout: float) -> str:
    if process.stdout is None:
        raise ReleaseProbeError("Staged Connector stdout was not captured.")
    result: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

    stdout = process.stdout

    def read_line() -> None:
        try:
            result.put(stdout.readline())
        except BaseException as exc:  # forward reader failures to the caller
            result.put(exc)

    threading.Thread(target=read_line, daemon=True).start()
    try:
        line = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise ReleaseProbeError(
            "Staged Connector did not become ready before timeout."
        ) from exc
    if isinstance(line, BaseException):
        raise ReleaseProbeError("Could not read staged Connector readiness.") from line
    return line


def _safe_child_environment() -> dict[str, str]:
    keys = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH")
    return {key: value for key in keys if (value := os.environ.get(key))}


def _normalize_tool(tool: Any) -> dict[str, Any]:
    fields = (
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "_meta",
    )
    value = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    return {key: value[key] for key in fields if key in value}


async def _discover(url: str, token: str) -> tuple[dict[str, Any], dict[str, int]]:
    import httpx2 as httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
        trust_env=False,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(read_stream, write_stream) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                resources = (await client.list_resources()).resources
                templates = (await client.list_resource_templates()).resource_templates
                prompts = (await client.list_prompts()).prompts

    normalized = sorted(
        (_normalize_tool(tool) for tool in tools), key=lambda item: item["name"]
    )
    return (
        {"normalizationVersion": 1, "tools": normalized},
        {
            "tools": len(tools),
            "resourcesAndTemplates": len(resources) + len(templates),
            "prompts": len(prompts),
        },
    )


def probe_managed_runtime(
    release_root: Path,
    connector_build_id: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Discover MCP metadata using the bundled interpreter without engineering access."""
    root = release_root.resolve(strict=True)
    executable = root / "runtime" / "python" / "python.exe"
    contracts = (root / "contracts").resolve(strict=True)
    protocol = contracts / "protocol.json"
    if not executable.is_file() or not protocol.is_file():
        raise ReleaseProbeError(
            "Staged private runtime or protocol contract is missing."
        )

    contract_digest = "sha256:" + hashlib.sha256(protocol.read_bytes()).hexdigest()
    provider_token = "P" * 40
    bridge = _ProbeBridge(connector_build_id, contract_digest)
    port = _free_port(bridge.endpoint[1])
    connector_id = "ci_0123456789abcdef0123456789abcdef"
    binding = {"packageId":"rhino","releaseDigest":"sha256:"+"0"*64,"connectorInstanceId":connector_id,"generation":1,"configurationRevision":1,"secretRevision":0}
    startup = {
        "schemaVersion": 3,
        "mode": "metadata",
        "binding": binding,
        "bind": {"host": "127.0.0.1", "port": port, "path": "/mcp"},
        "bearerToken": provider_token,
        "connectorBuildId": connector_build_id,
        "configuration": {},
        "secrets": [],
        "dataDirectory": None,
        "nativePeer": None,
    }
    command = [
        str(executable),
        "-I",
        "-s",
        "-B",
        "-m",
        "rhino_mcp.managed_bootstrap",
    ]
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_safe_child_environment(),
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(startup, separators=(",", ":")) + "\n")
        process.stdin.flush()

        line = _read_line_with_timeout(process, timeout=20)
        if not line:
            raise ReleaseProbeError("Staged Connector exited before readiness.")
        try:
            ready = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReleaseProbeError(
                "Staged Connector emitted invalid readiness JSON."
            ) from exc
        if (
            ready.get("kind") != "ready"
            or ready.get("schemaVersion") != 3
            or ready.get("connectorBuildId") != connector_build_id
            or ready.get("binding") != binding
            or ready.get("port") != port
        ):
            raise ReleaseProbeError(
                "Staged Connector readiness did not match its startup identity."
            )
        if bridge.authenticated.is_set():
            raise ReleaseProbeError(
                "Metadata startup unexpectedly connected to a Bridge."
            )

        catalog, counts = asyncio.run(
            _discover(f"http://127.0.0.1:{port}/mcp", provider_token)
        )
        return catalog, counts
    except (OSError, asyncio.TimeoutError) as exc:
        raise ReleaseProbeError("Staged Connector MCP probe failed.") from exc
    finally:
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        bridge.close()
