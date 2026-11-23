"""Rhino-native control handshake owned by the Rhino package."""

import json
import secrets
import struct
import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from time import monotonic, sleep

from rhino_mcp.bridge import BridgeBusy, BridgeClient, BridgeConfig, BridgeResultUnknown, BridgeUnavailable
from rhino_mcp.build_identity import CONNECTOR_BUILD_ID
from rhino_mcp.setup import installed_rhino


def _process_image(process) -> Path:
    query = ctypes.WinDLL("kernel32", use_last_error=True).QueryFullProcessImageNameW
    query.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    path = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(path))
    if not query(int(process), 0, path, ctypes.byref(length)):
        raise OSError("rhinoBridgeUnexpectedProcess")
    return Path(path.value).resolve()


def _read(pipe, size):
    result = bytearray()
    while len(result) < size:
        chunk = pipe.read(size - len(result))
        if not chunk:
            raise OSError("rhinoBridgeClosed")
        result.extend(chunk)
    return bytes(result)


def _frame(pipe):
    size, = struct.unpack(">I", _read(pipe, 4))
    if not 0 < size <= 65536:
        raise ValueError("invalidRhinoBridgeFrame")
    return json.loads(_read(pipe, size))


def _send(pipe, value):
    raw = json.dumps(value, separators=(",", ":")).encode()
    pipe.write(struct.pack(">I", len(raw)) + raw)


@contextmanager
def await_bridge():
    import pywintypes
    import win32api
    import win32file
    import win32pipe
    import win32security

    expected = installed_rhino()
    if expected is None:
        raise BridgeUnavailable("Rhino is missing or its selected installation is no longer available.")
    security = pywintypes.SECURITY_ATTRIBUTES()
    security.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:P(A;;GA;;;OW)", win32security.SDDL_REVISION_1
    )
    deadline = monotonic() + 10
    while True:
        try:
            handle = win32pipe.CreateNamedPipe(
                r"\\.\pipe\rhino-mcp-control-v1",
                win32pipe.PIPE_ACCESS_DUPLEX | 0x00080000, 8 | win32pipe.PIPE_NOWAIT,
                1, 65536, 65536, 0, security,
            )
            break
        except pywintypes.error as error:
            # The shared native control pipe is serialized before any command is sent.
            if error.winerror not in (5, 231) or monotonic() >= deadline:
                raise
            sleep(0.05)
    try:
        while True:
            try:
                win32pipe.ConnectNamedPipe(handle)
                break
            except pywintypes.error as error:
                if error.winerror == 535:
                    break
                if error.winerror != 536 or monotonic() >= deadline:
                    raise BridgeUnavailable("Start Rhino and load the Bridge before calling a Rhino tool.") from error
                sleep(0.05)
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_WAIT, None, None)
        pid = win32pipe.GetNamedPipeClientProcessId(handle)
        process = win32api.OpenProcess(0x1000, False, pid)
        try:
            image = _process_image(process)
        finally:
            process.Close()
        if image != expected:
            raise ValueError("rhinoBridgeUnexpectedProcess")
        # Use a duplicate so Python's file wrapper and the original pipe have independent ownership.
        import msvcrt
        import os

        current = win32api.GetCurrentProcess()
        duplicate = win32api.DuplicateHandle(current, handle, current, 0, False, 2)
        fd = msvcrt.open_osfhandle(duplicate.Detach(), os.O_BINARY | os.O_RDWR)
        with os.fdopen(fd, "r+b", buffering=0) as pipe:
            registration = _frame(pipe)
            if any(registration.get(key) != value for key, value in {
                "kind": "register", "protocol": "rhino-mcp-control-v1", "rhinoMajor": 8,
                "pluginId": "74a1e8f4-1134-4686-a3e5-ee8bf393dec9",
                "connectorBuildId": CONNECTOR_BUILD_ID,
            }.items()):
                raise ValueError("invalidRhinoBridgeRegistration")
            generation = secrets.randbelow(2**53 - 1) + 1
            token = secrets.token_hex(32)
            contract = registration["contractDigest"]
            _send(pipe, {
                "schemaVersion": 1, "kind": "lease", "generation": generation,
                "token": token, "connectorBuildId": CONNECTOR_BUILD_ID, "contractDigest": contract,
            })
            ready = _frame(pipe)
            if (
                ready.get("kind") != "bridgeReady" or ready.get("generation") != generation
                or ready.get("capabilityDigest") != registration.get("capabilityDigest")
            ):
                raise ValueError("invalidRhinoBridgeReady")
            if ready.get("previousSessionState") != "quiescent":
                raise BridgeResultUnknown("A previous Rhino result remains unknown; further dispatch is blocked.")
            bridge = BridgeClient(BridgeConfig(
                ("127.0.0.1", ready["port"]), 15, generation, token, CONNECTOR_BUILD_ID, contract,
            ))
            try:
                yield bridge
            finally:
                bridge.disconnect()
    finally:
        win32file.CloseHandle(handle)


class StdioBridgeSlot:
    """Acquire the exclusive Rhino Bridge only while dispatching a command."""

    def send_command(self, command_type, params=None):
        import pywintypes

        from rhino_mcp.server import RhinoConnection

        try:
            with await_bridge() as bridge:
                result = RhinoConnection(bridge).send_command(command_type, params)
                if bridge.lifecycle_snapshot()[0] == "unknown":
                    raise BridgeResultUnknown("The Rhino result is unknown. Do not retry automatically.")
                return result
        except pywintypes.error as error:
            if error.winerror in (5, 231):
                raise BridgeBusy("Rhino Bridge is serving another command.") from error
            raise
