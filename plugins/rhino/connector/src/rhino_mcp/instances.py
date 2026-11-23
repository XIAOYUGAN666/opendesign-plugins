"""Windows Rhino instance status and safe target selection for the Connector."""

import ctypes
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rhino_mcp._connector_process import running_records, select_instance

_CONTROL_PREFIX = "rhino-mcp-control-v1"
_STATUS_TTL = timedelta(seconds=8)


def _local_app_data() -> Path:
    if configured := os.environ.get("LOCALAPPDATA"):
        return Path(configured)
    # OpenDesign launches both the Agent Server and connectors with a minimal environment.
    # Ask Windows for the same known folder used by the native plug-in.
    path = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x001C, None, 0, path):
        raise OSError("rhinoInstanceDirectoryUnavailable")
    return Path(path.value)


def _read_instance(pid: int, created: int) -> dict:
    try:
        status_dir = _local_app_data() / "RhinoMcpBridge" / "instances"
        with (status_dir / f"{pid}.json").open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            return {}
        value = json.loads(raw)
        updated = datetime.fromisoformat(value["updatedAt"])
        if updated.tzinfo is None or abs(datetime.now(timezone.utc) - updated) > _STATUS_TTL:
            return {}
        if value["pid"] != pid or value["created"] != str(created):
            return {}
        return value
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def instances() -> list[tuple[str, int, dict]]:
    results = []
    for name, pid, created in running_records("Rhino.exe"):
        if pid <= 0 or created <= 0:
            continue
        identity = select_instance([(name, pid, created)], "Rhino.exe")
        if identity is not None:
            results.append((identity, pid, _read_instance(pid, created)))
    return results


@dataclass(frozen=True)
class Target:
    identity: str
    pid: int
    port: int

    @property
    def pipe(self) -> str:
        return f"{_CONTROL_PREFIX}-{self.pid}"


def resolve_target(port: int | None = None, identity: str | None = None) -> Target:
    listening = [Target(instance, pid, status["port"]) for instance, pid, status in instances()
                 if type(status.get("port")) is int and (identity is None or instance == identity)]
    if port is not None:
        target = [instance for instance in listening if instance.port == port]
    else:
        target = sorted(listening, key=lambda item: item.port)[:1]
    if len(target) == 1:
        return target[0]
    from rhino_mcp.bridge import BridgeUnavailable

    raise BridgeUnavailable(
        "未找到正在监听的目标 Rhino 实例。请在 Rhino 插件的连接设置中检查目标端口和 Bridge 状态。"
    )


def choose_target(port: int | None = None) -> tuple[int, str]:
    target = resolve_target(port)
    return target.pid, target.pipe
