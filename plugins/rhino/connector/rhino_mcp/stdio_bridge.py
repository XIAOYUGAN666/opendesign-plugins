"""Rhino-native control handshake owned by the Rhino package."""

import json
import secrets
import struct
import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from time import monotonic, sleep

from rhino_mcp.bridge import BridgeClient, BridgeConfig
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
        raise OSError("rhinoNotInstalled")
    security = pywintypes.SECURITY_ATTRIBUTES()
    security.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:P(A;;GA;;;OW)", win32security.SDDL_REVISION_1
    )
    handle = win32pipe.CreateNamedPipe(
        r"\\.\pipe\rhino-mcp-control-v1",
        win32pipe.PIPE_ACCESS_DUPLEX | 0x00080000, 8 | win32pipe.PIPE_NOWAIT,
        1, 65536, 65536, 0, security,
    )
    try:
        deadline = monotonic() + 10
        while True:
            try:
                win32pipe.ConnectNamedPipe(handle)
                break
            except pywintypes.error as error:
                if error.winerror == 535:
                    break
                if error.winerror != 536 or monotonic() >= deadline:
                    raise OSError("rhinoBridgeUnavailable") from error
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
            yield BridgeClient(BridgeConfig(
                ("127.0.0.1", ready["port"]), 15, generation, token, CONNECTOR_BUILD_ID, contract,
            ))
    finally:
        win32file.CloseHandle(handle)
