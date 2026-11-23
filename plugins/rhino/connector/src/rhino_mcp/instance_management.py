"""Setup actions for the Rhino instance list and native commands."""

import ctypes
import msvcrt
import os
from ctypes import wintypes

from rhino_mcp.configuration import ConfigurationV1
from rhino_mcp.instances import instances

COMMANDS = {"mcpstart", "mcpstop", "mcpversion", "mcptest"}


def _window(pid: int, status: dict) -> int | None:
    import win32gui
    import win32process

    handle = status.get("windowHandle")
    if type(handle) is int and win32gui.IsWindow(handle):
        if win32process.GetWindowThreadProcessId(handle)[1] == pid:
            return handle
    if status:
        return None
    windows = []

    def collect(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd)
                and win32process.GetWindowThreadProcessId(hwnd)[1] == pid):
            windows.append(hwnd)
        return True

    win32gui.EnumWindows(collect, None)
    return windows[0] if windows else None


def _status() -> dict:
    settings = ConfigurationV1.read()
    records = instances()
    listening = [state["port"] for _, _, state in records if type(state.get("port")) is int]
    port = settings.targetPort or min(listening, default=None)
    items = []
    for identity, pid, state in records:
        active = state.get("clients") or []
        latest = state.get("activity") or {}
        fields = {
            "softwareVersion": state.get("softwareVersion"),
            "bridgeVersion": state.get("bridgeVersion"),
            "grasshopperLoaded": state.get("grasshopperLoaded", False),
            "grasshopperDocumentName": state.get("grasshopperDocumentName"),
            "port": state.get("port"),
            "portSource": state.get("portSource"),
            "startedAt": state.get("startedAt"),
            "connectionScope": "仅本机",
            "lastCommandAt": latest.get("lastCommandAt"),
            "lastCommand": latest.get("lastCommand"),
            "commandCount": latest.get("commandCount", 0),
            "bridgeLoaded": bool(state),
        }
        clients = [{key: client.get(key) for key in ("connectedAt", "lastCommandAt", "lastCommand", "commandCount")}
                   for client in active]
        items.append({"id": identity, "label": state.get("documentName") or "Rhino 8", "fields": fields,
                      "primary": port is not None and port == state.get("port"),
                      "actionsAvailable": bool(state), "clients": clients})
    return {"type": "completed", "items": items}


def _failure(reason: str, message: str) -> dict:
    return {"type": "failed", "reason": reason, "message": message}


def _open_management(pid: int, created: str, expected):
    import win32api
    import win32con
    import win32file
    import win32pipe

    from rhino_mcp.stdio_bridge import _process_image

    path = rf"\\.\pipe\rhino-mcp-management-v1-{pid}-{created}"
    win32pipe.WaitNamedPipe(path, 2000)
    handle = win32file.CreateFile(path, win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                  0, None, win32con.OPEN_EXISTING, 0, None)
    try:
        peer = wintypes.ULONG()
        query = ctypes.WinDLL("kernel32", use_last_error=True).GetNamedPipeServerProcessId
        query.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
        if not query(int(handle), ctypes.byref(peer)) or peer.value != pid:
            raise OSError("unexpectedRhinoInstance")
        process = win32api.OpenProcess(0x1000, False, pid)
        try:
            if _process_image(process) != expected:
                raise OSError("unexpectedRhinoExecutable")
        finally:
            process.Close()
        fd = msvcrt.open_osfhandle(handle.Detach(), os.O_BINARY | os.O_RDWR)
        return os.fdopen(fd, "r+b", buffering=0)
    finally:
        handle.Close()


def run_action(action: str, arguments: dict, expected) -> dict:
    import pywintypes

    if action == "status":
        return _status()
    identity = arguments.get("instanceId")
    selected = next(((pid, state) for key, pid, state in instances() if key == identity), None)
    if selected is None:
        return _failure("rhinoInstanceUnavailable", "目标 Rhino 实例已退出。")
    pid, status = selected
    try:
        if action == "locate":
            import win32con
            import win32gui

            hwnd = _window(pid, status)
            if hwnd is None:
                return _failure("rhinoWindowUnavailable", "找不到目标 Rhino 窗口。")
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return {"type": "completed"}
        if action not in COMMANDS or not status:
            return _failure("bridgeNotLoaded", "目标 Rhino 实例的 Bridge 尚未加载。")
        from rhino_mcp.stdio_bridge import _frame, _send

        request = {"action": action, **{key: value for key, value in arguments.items() if key != "instanceId"}}
        with _open_management(pid, status["created"], expected) as pipe:
            _send(pipe, request)
            reply = _frame(pipe)
        if type(reply.get("success")) is not bool or not isinstance(reply.get("output"), str):
            raise ValueError("invalidRhinoManagementReply")
        return {"type": "completed" if reply["success"] else "failed", "output": reply["output"]}
    except (pywintypes.error, OSError, ValueError, KeyError):
        return _failure("rhinoActionUnconfirmed", "无法确认 Rhino 命令结果。")
