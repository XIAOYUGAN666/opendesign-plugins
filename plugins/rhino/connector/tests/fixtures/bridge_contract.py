"""Independent fake Bridge helpers for Connector transport tests.

The helper deliberately implements framing itself instead of importing the
production parser, so a shared framing bug cannot make these tests pass.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

from rhino_mcp.bridge import BridgeConfig

CONTRACT_ROOT = Path(__file__).resolve().parents[3] / "contracts"
GENERATION = 1
TOKEN = "test-bridge-token-0123456789abcdef"
CONNECTOR_BUILD_ID = "sha256:" + "0" * 64
CONTRACT_DIGEST = (
    "sha256:"
    + hashlib.sha256((CONTRACT_ROOT / "protocol.json").read_bytes()).hexdigest()
)
MAX_FRAME_SIZE = 64 * 1024 * 1024


def bridge_config(
    endpoint: tuple[str, int],
    *,
    generation: int = GENERATION,
    token: str = TOKEN,
    connector_build_id: str = CONNECTOR_BUILD_ID,
    contract_digest: str = CONTRACT_DIGEST,
    contract_root: Path = CONTRACT_ROOT,
) -> BridgeConfig:
    return BridgeConfig(
        endpoint=endpoint,
        timeout_seconds=2.0,
        generation=generation,
        token=token,
        connector_build_id=connector_build_id,
        contract_digest=contract_digest,
        contract_root=contract_root.resolve(),
    )


def _read_exact(sock: socket.socket, size: int) -> bytes:
    received = bytearray()
    while len(received) < size:
        chunk = sock.recv(size - len(received))
        if not chunk:
            raise ConnectionError("fake Bridge peer closed mid-frame")
        received.extend(chunk)
    return bytes(received)


def _receive_frame(sock: socket.socket) -> dict[str, Any]:
    size = int.from_bytes(_read_exact(sock, 4), "big")
    if size <= 0 or size > MAX_FRAME_SIZE:
        raise ValueError("invalid fake Bridge frame size")
    value = json.loads(_read_exact(sock, size).decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("fake Bridge frame must contain a JSON object")
    return value


def _send_frame(sock: socket.socket, value: dict[str, Any]) -> None:
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    sock.sendall(len(payload).to_bytes(4, "big") + payload)


class FakeBridge:
    """Small auth-first loopback peer with observable connections and commands."""

    def __init__(
        self,
        *,
        response: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        close_after_command: bool = False,
        raw_auth_response: bytes | None = None,
        reject_authentication: bool = False,
        probe_response: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.response = response or (
            lambda _command: {"status": "success", "result": {}}
        )
        self.close_after_command = close_after_command
        self.raw_auth_response = raw_auth_response
        self.reject_authentication = reject_authentication
        self.probe_response = probe_response
        self.connection_count = 0
        self.authentication_frames: list[dict[str, Any]] = []
        self.command_frames: list[dict[str, Any]] = []
        self.connection_probes: list[dict[str, Any]] = []
        self.pre_auth_command_rejected = False
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._accepted = threading.Event()
        self._endpoint: tuple[str, int] | None = None
        self._error: BaseException | None = None

    @property
    def endpoint(self) -> tuple[str, int]:
        if self._endpoint is None:
            raise RuntimeError("FakeBridge has not started")
        return self._endpoint

    def start(self) -> FakeBridge:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.settimeout(0.1)
        self._listener = listener
        self._endpoint = ("127.0.0.1", listener.getsockname()[1])
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    def wait_for_connection(self, timeout: float = 1.0) -> bool:
        return self._accepted.wait(timeout)

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return

            self.connection_count += 1
            self._accepted.set()
            try:
                with connection:
                    connection.settimeout(2.0)
                    self._handle(connection)
            except (ConnectionError, OSError, ValueError, json.JSONDecodeError) as exc:
                if not self._stop.is_set():
                    self._error = exc

    def _handle(self, connection: socket.socket) -> None:
        first = _receive_frame(connection)
        self.authentication_frames.append(first)
        if first.get("type") != "authenticate":
            self.pre_auth_command_rejected = True
            return

        credentials_match = (
            first.get("generation") == GENERATION
            and first.get("token") == TOKEN
            and first.get("connectorBuildId") == CONNECTOR_BUILD_ID
            and first.get("contractDigest") == CONTRACT_DIGEST
        )
        if self.reject_authentication or not credentials_match:
            _send_frame(connection, {"type": "authenticationRejected"})
            return
        if self.raw_auth_response is not None:
            connection.sendall(self.raw_auth_response)
            return

        _send_frame(
            connection,
            {
                "type": "authenticated",
                "generation": GENERATION,
                "connectorBuildId": CONNECTOR_BUILD_ID,
                "contractDigest": CONTRACT_DIGEST,
            },
        )

        while not self._stop.is_set():
            try:
                command = _receive_frame(connection)
            except ConnectionError:
                return
            if command.get("type") == "connectionProbe":
                self.connection_probes.append(command)
                response = self.probe_response(command) if self.probe_response else {"type": "connectionProbeAck", "nonce": command.get("nonce")}
                _send_frame(connection, response)
                continue
            self.command_frames.append(command)
            if self.close_after_command:
                return
            _send_frame(connection, self.response(command))
