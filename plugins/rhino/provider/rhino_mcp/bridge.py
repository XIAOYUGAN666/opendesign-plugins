"""Authenticated, length-framed transport from the Provider to Rhino Bridge."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import jsonschema

from rhino_mcp.validation import (
    CONTRACTS_DIR,
    SchemaInfrastructureError,
    validate_command,
    validate_contract_inventory,
    validate_response,
)

FRAME_HEADER_SIZE = 4
MAX_FRAME_SIZE = 64 * 1024 * 1024
_AUTH_REQUEST_TYPE = "authenticate"
_AUTHENTICATED_TYPE = "authenticated"
_AUTH_REJECTED_TYPE = "authenticationRejected"
_AUTH_ACK_FIELDS = frozenset(
    {"type", "generation", "providerBuildId", "contractDigest"}
)
_SAFE_ENVELOPE_FIELDS = frozenset({"include_delta", "include_health"})
_RESULT_METADATA_FIELDS = frozenset({"_delta", "_health"})
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>|]*")


class BridgeError(RuntimeError):
    category = "bridgeError"


class BridgeUnavailable(BridgeError):
    """The configured loopback endpoint could not be reached before dispatch."""

    category = "bridgeUnavailable"


class BridgeRejected(BridgeError):
    """The Bridge rejected authentication before any command was dispatched."""

    category = "bridgeRejected"


class BridgeProtocolError(BridgeError):
    """The Bridge sent malformed protocol data or local schemas are unavailable."""

    category = "bridgeProtocolError"

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category


class BridgeContractViolation(BridgeProtocolError):
    """A command or received response does not satisfy the locked contract."""

    category = "contractViolation"


class BridgeResultUnknown(BridgeError):
    """Command bytes may have reached Rhino, but no trustworthy result arrived."""

    category = "resultUnknown"


class RhinoCommandError(BridgeError):
    """Rhino returned a well-formed, typed command error response."""

    category = "rhinoCommandError"


@dataclass(frozen=True)
class BridgeConfig:
    """Validated one-generation Bridge configuration supplied by managed startup."""

    endpoint: tuple[str, int]
    timeout_seconds: float
    generation: int
    token: str = field(repr=False)
    provider_build_id: str
    contract_digest: str
    contract_root: Path = CONTRACTS_DIR

    def __post_init__(self) -> None:
        if (
            not isinstance(self.endpoint, tuple)
            or len(self.endpoint) != 2
            or not isinstance(self.endpoint[0], str)
            or not isinstance(self.endpoint[1], int)
            or isinstance(self.endpoint[1], bool)
        ):
            raise ValueError(
                "Bridge endpoint must contain an IPv4 loopback address and port."
            )
        try:
            address = ipaddress.ip_address(self.endpoint[0])
        except ValueError as exc:
            raise ValueError("Bridge endpoint must be IPv4 loopback.") from exc
        if address != ipaddress.IPv4Address("127.0.0.1"):
            raise ValueError("Bridge endpoint must be IPv4 loopback.")
        if not 1 <= self.endpoint[1] <= 65535:
            raise ValueError("Bridge port is outside the supported range.")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or not 0.1 <= self.timeout_seconds <= 120.0
        ):
            raise ValueError("Bridge timeout is outside the supported range.")
        if (
            not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or not 1 <= self.generation <= (1 << 64) - 1
        ):
            raise ValueError("Bridge generation is invalid.")
        if not isinstance(self.token, str) or not 32 <= len(self.token) <= 256:
            raise ValueError("Bridge credential is invalid.")
        if not isinstance(self.provider_build_id, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.provider_build_id
        ):
            raise ValueError("Provider build identity is invalid.")
        if not isinstance(self.contract_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.contract_digest
        ):
            raise ValueError("Bridge contract identity is invalid.")
        root = Path(self.contract_root)
        if not root.is_absolute():
            raise ValueError("Contract root must be an absolute path.")
        try:
            canonical_root = root.resolve(strict=True)
        except OSError as exc:
            raise ValueError("Contract root is unavailable.") from exc
        if not canonical_root.is_dir():
            raise ValueError("Contract root is unavailable.")
        object.__setattr__(self, "contract_root", canonical_root)


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains a duplicate member.")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"JSON constant '{value}' is not permitted.")


def encode_frame(payload: bytes) -> bytes:
    """Encode one UTF-8 JSON payload using the Bridge's 4-byte big-endian frame."""
    if not isinstance(payload, bytes):
        raise TypeError("Bridge frame payload must be bytes.")
    if not 0 < len(payload) <= MAX_FRAME_SIZE:
        raise BridgeProtocolError("Bridge frame length is outside the supported range.")
    return len(payload).to_bytes(FRAME_HEADER_SIZE, "big") + payload


def read_exact(sock: Any, num_bytes: int) -> bytes:
    """Read exactly ``num_bytes`` despite TCP returning partial segments."""
    if not isinstance(num_bytes, int) or isinstance(num_bytes, bool) or num_bytes < 0:
        raise ValueError("Read length must be a non-negative integer.")
    received = bytearray()
    while len(received) < num_bytes:
        chunk = sock.recv(num_bytes - len(received))
        if not chunk:
            raise ConnectionError("Bridge closed the connection mid-frame.")
        received.extend(chunk)
    return bytes(received)


def read_frame_bytes(sock: Any) -> bytes:
    """Read one bounded frame body without interpreting its payload."""
    header = read_exact(sock, FRAME_HEADER_SIZE)
    frame_length = int.from_bytes(header, "big", signed=False)
    if not 0 < frame_length <= MAX_FRAME_SIZE:
        raise BridgeProtocolError("Bridge frame length is outside the supported range.")
    return read_exact(sock, frame_length)


def read_frame(sock: Any) -> dict[str, Any]:
    """Read one frame as a strict UTF-8 JSON object.

    Duplicate object names and JavaScript-only NaN/Infinity constants are
    rejected instead of being silently normalized by the standard decoder.
    """
    payload = read_frame_bytes(sock)
    try:
        decoded = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BridgeProtocolError("Bridge frame is not valid UTF-8.") from exc
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_constant=_reject_non_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise BridgeProtocolError("Bridge frame is not valid strict JSON.") from exc
    if not isinstance(value, dict):
        raise BridgeProtocolError("Bridge frame must contain a JSON object.")
    return value


def _encode_json_frame(value: Mapping[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise BridgeContractViolation(
            "Command data cannot be encoded as contract JSON."
        ) from exc
    return encode_frame(payload)


def _safe_rhino_message(value: Any, token: str) -> str:
    if not isinstance(value, str):
        return "Rhino command failed."
    message = " ".join("".join(ch for ch in value if ch >= " " or ch == "\t").split())
    if token:
        message = message.replace(token, "[redacted]")
    message = _WINDOWS_PATH.sub("[local path]", message)
    message = message[:512].strip()
    return message or "Rhino command failed."


def normalize_negative_zero(value: Any) -> Any:
    """Normalize signed zero recursively without changing non-zero values."""
    if isinstance(value, float):
        return 0.0 if value == 0.0 else value
    if isinstance(value, dict):
        return {key: normalize_negative_zero(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_negative_zero(item) for item in value]
    return value


class BridgeClient:
    """One serialized client for the current authenticated Rhino generation."""

    def __init__(self, config: BridgeConfig) -> None:
        if not isinstance(config, BridgeConfig):
            raise TypeError("BridgeClient requires validated BridgeConfig.")
        self._config = config
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._in_flight = 0
        self._result_unknown = False
        self._inventory = validate_contract_inventory(config.contract_root)

    @property
    def is_connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        """Open a socket and authenticate before any command frame can be sent."""
        with self._lock:
            self._connect_locked()

    def _connect_locked(self) -> None:
        if self._socket is not None:
            return
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(float(self._config.timeout_seconds))
            sock.connect(self._config.endpoint)
        except (OSError, TimeoutError) as exc:
            if sock is not None:
                sock.close()
            raise BridgeUnavailable(
                "Rhino Bridge is not connected. Open Rhino and confirm its plug-in is active, then retry this request."
            ) from exc

        assert sock is not None
        self._socket = sock
        auth_request = {
            "type": _AUTH_REQUEST_TYPE,
            "generation": self._config.generation,
            "token": self._config.token,
            "providerBuildId": self._config.provider_build_id,
            "contractDigest": self._config.contract_digest,
        }
        try:
            sock.sendall(_encode_json_frame(auth_request))
            response = read_frame(sock)
            if response.get("type") == _AUTH_REJECTED_TYPE:
                raise BridgeRejected(
                    "Rhino Bridge rejected the current generation credentials."
                )
            if response.get("type") != _AUTHENTICATED_TYPE:
                raise BridgeProtocolError(
                    "Rhino Bridge returned an invalid authentication response."
                )
            if set(response) != _AUTH_ACK_FIELDS:
                raise BridgeProtocolError(
                    "Rhino Bridge returned an invalid authentication response."
                )
            if (
                response.get("generation") != self._config.generation
                or response.get("providerBuildId") != self._config.provider_build_id
                or response.get("contractDigest") != self._config.contract_digest
            ):
                raise BridgeRejected(
                    "Rhino Bridge identity does not match the current generation."
                )
        except (BridgeRejected, BridgeProtocolError):
            self._drop_socket_locked()
            raise
        except (OSError, TimeoutError, ConnectionError) as exc:
            self._drop_socket_locked()
            raise BridgeUnavailable(
                "Rhino Bridge authentication could not be completed. Confirm the plug-in is active, then retry this request."
            ) from exc

    def call(
        self,
        command_type: str,
        params: Mapping[str, Any] | None = None,
        *,
        envelope_fields: Mapping[str, Any] | None = None,
    ) -> Any:
        """Validate, send once, and return the matching response without replay."""
        command_params = dict(params or {})
        try:
            validate_command(
                command_type,
                command_params,
                raise_on_error=True,
                contract_root=self._config.contract_root,
            )
        except SchemaInfrastructureError as exc:
            raise BridgeProtocolError(
                "Provider command contracts are unavailable; the command was not sent.",
                category="schemaInfrastructureError",
            ) from exc
        except jsonschema.ValidationError as exc:
            path = ".".join(str(part) for part in exc.absolute_path)
            location = f" at '{path}'" if path else f" for '{command_type}'"
            raise BridgeContractViolation(
                f"Command contract violation{location}; the command was not sent."
            ) from exc

        command: dict[str, Any] = {"type": command_type, "params": command_params}
        if envelope_fields:
            if not set(envelope_fields).issubset(_SAFE_ENVELOPE_FIELDS) or any(
                not isinstance(value, bool) for value in envelope_fields.values()
            ):
                raise BridgeContractViolation(
                    "Command envelope fields are invalid; the command was not sent."
                )
            command.update(envelope_fields)
        frame = _encode_json_frame(command)

        with self._lock:
            with self._status_lock:
                if self._result_unknown:
                    raise BridgeResultUnknown(
                        "A previous Rhino result remains unknown; further dispatch is blocked."
                    )
                self._in_flight = 1
            try:
                return self._dispatch_locked(command_type, frame)
            except BridgeResultUnknown:
                with self._status_lock:
                    self._result_unknown = True
                self._drop_socket_locked()
                raise
            finally:
                with self._status_lock:
                    self._in_flight = 0

    def lifecycle_snapshot(self) -> tuple[Literal["idle", "running", "unknown"], int]:
        """Project the same transport owner's unsettled dispatch state."""
        with self._status_lock:
            state: Literal["idle", "running", "unknown"] = (
                "unknown"
                if self._result_unknown
                else "running"
                if self._in_flight
                else "idle"
            )
            return state, self._in_flight

    def _dispatch_locked(self, command_type: str, frame: bytes) -> Any:
        self._connect_locked()
        sock = self._socket
        if sock is None:
            raise BridgeUnavailable("Rhino Bridge connection is unavailable.")
        try:
            # Once sendall starts, Python cannot tell how many bytes reached
            # the peer. Never reconnect or replay within this call.
            sock.sendall(frame)
        except (OSError, TimeoutError) as exc:
            self._drop_socket_locked()
            raise BridgeResultUnknown(
                "Rhino may have received the command, but no trustworthy result arrived. Do not retry automatically."
            ) from exc

        try:
            return self._receive_result_locked(command_type, sock)
        except (RhinoCommandError, BridgeResultUnknown):
            raise
        except Exception as exc:
            # Any response-processing failure occurs after command dispatch.
            # An unexpected decoder/validator error cannot prove settlement.
            raise BridgeResultUnknown(
                "Rhino returned an unverifiable result; the action may already have occurred. Do not retry automatically."
            ) from exc

    def _receive_result_locked(self, command_type: str, sock: socket.socket) -> Any:
        try:
            response = read_frame(sock)
        except (BridgeProtocolError, OSError, TimeoutError, ConnectionError) as exc:
            self._drop_socket_locked()
            raise BridgeResultUnknown(
                "Rhino may have executed the command, but no trustworthy result arrived. Do not retry automatically."
            ) from exc

        result = normalize_negative_zero(self._parse_command_response(response))
        validation_result = result
        if isinstance(result, dict) and any(
            key in result for key in _RESULT_METADATA_FIELDS
        ):
            validation_result = {
                key: value
                for key, value in result.items()
                if key not in _RESULT_METADATA_FIELDS
            }
        try:
            validate_response(
                command_type,
                validation_result,
                raise_on_error=True,
                contract_root=self._config.contract_root,
            )
        except SchemaInfrastructureError as exc:
            raise BridgeResultUnknown(
                "Rhino returned an unverifiable result; the action may already have occurred. Do not retry automatically."
            ) from exc
        except jsonschema.ValidationError as exc:
            raise BridgeResultUnknown(
                "Rhino returned a response that violates the contract; the action may already have occurred. Do not retry automatically."
            ) from exc
        return result

    def _parse_command_response(self, response: dict[str, Any]) -> Any:
        if response.get("status") == "error":
            if set(response) != {"status", "message"} or not isinstance(
                response.get("message"), str
            ):
                self._drop_socket_locked()
                raise BridgeResultUnknown(
                    "Rhino may have executed the command, but returned an invalid error response. Do not retry automatically."
                )
            raise RhinoCommandError(
                _safe_rhino_message(response["message"], self._config.token)
            )
        if response.get("status") != "success" or set(response) != {"status", "result"}:
            self._drop_socket_locked()
            raise BridgeResultUnknown(
                "Rhino may have executed the command, but returned an invalid response. Do not retry automatically."
            )
        return response["result"]

    def send_command(
        self,
        command_type: str,
        params: Mapping[str, Any] | None = None,
        *,
        envelope_fields: Mapping[str, Any] | None = None,
    ) -> Any:
        """Compatibility name retained for existing tool wrappers."""
        return self.call(command_type, params, envelope_fields=envelope_fields)

    def disconnect(self) -> None:
        with self._lock:
            self._drop_socket_locked()

    close = disconnect

    def _drop_socket_locked(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
