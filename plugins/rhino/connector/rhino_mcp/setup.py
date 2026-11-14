"""Package-owned Rhino setup diagnostics."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

BRIDGE_PACKAGE = "rhino-mcp-bridge"


def diagnose_bridge(*, rhino_running: bool, bridge_ready: bool) -> dict:
    if not rhino_running:
        return {"type": "failed", "reason": "softwareNotRunning", "message": "请启动 Rhino"}
    if not bridge_ready:
        return {"type": "failed", "reason": "bridgeNotLoaded", "message": "Bridge 尚未加载"}
    return {"type": "completed"}


def installed_rhino() -> Path | None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\McNeel\Rhinoceros\8.0\Install",
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            root, _ = winreg.QueryValueEx(key, "InstallPath")
    except FileNotFoundError:
        return None
    for executable in (Path(root) / "System" / "Rhino.exe", Path(root) / "Rhino.exe"):
        if executable.is_file():
            return executable.resolve()
    return None


def _yak(executable: Path, *arguments: str) -> bytes:
    environment = {name: value for name, value in os.environ.items() if name.upper() != "YAK_TOKEN"}
    result = subprocess.run(
        (str(executable.with_name("Yak.exe")), *arguments), capture_output=True,
        timeout=120, check=False, creationflags=subprocess.CREATE_NO_WINDOW, env=environment,
    )
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError("yakOperationFailed")
    return result.stdout


def _bridge_version(executable: Path) -> str | None:
    listing = _yak(executable, "list")
    lines = listing.strip().splitlines()
    if not lines or not lines[0].strip().startswith(b"Package directory:"):
        raise ValueError("invalidYakListing")
    matches = []
    for line in lines[1:]:
        line = line.strip()
        if line.startswith(b"rhino-mcp-bridge "):
            match = re.fullmatch(rb"rhino-mcp-bridge \(([0-9]+\.[0-9]+\.[0-9]+)\)", line)
            if match is None:
                raise ValueError("invalidYakListing")
            matches.append(match[1].decode("ascii"))
    if len(matches) > 1:
        raise ValueError("invalidYakListing")
    return next(iter(matches), None)


def _save_binding(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def prepare_bridge(request: dict, executable: Path, binding: Path) -> dict:
    candidates = sorted(Path.cwd().glob("native/rhino-mcp-bridge-*.yak"))
    if not executable.with_name("Yak.exe").is_file() or len(candidates) != 1:
        return {"type": "failed", "reason": "bridgePackageUnavailable", "message": "缺少 Yak 或签名制品中的 Bridge 安装包"}
    version = request["packageVersion"]
    current = _bridge_version(executable)
    previous = json.loads(binding.read_bytes()) if binding.exists() else {}
    same_yak = previous.get("executable") == str(executable)
    owned = same_yak and previous.get("owned") is True
    can_reconcile = same_yak and previous.get("operationId") == request["operationId"] and previous.get("pending") == version
    if current != version:
        owned = current is None or (owned and previous.get("version") == current)
        _save_binding(binding, {"executable": str(executable), "version": current, "pending": version,
                                "operationId": request["operationId"], "owned": owned})
        _yak(executable, "install", str(candidates[0]))
        if _bridge_version(executable) != version:
            raise ValueError("bridgeInstallUnconfirmed")
    else:
        owned = owned and (previous.get("version") == version or can_reconcile)
    _save_binding(binding, {"executable": str(executable), "version": version, "owned": owned})
    return {"type": "completed"}


def remove_bridge(step: str, executable: Path | None, binding: Path) -> dict:
    state = json.loads(binding.read_bytes()) if binding.exists() else {}
    current = _bridge_version(executable) if executable is not None and executable.with_name("Yak.exe").is_file() else None
    removable = current is not None and state == {"executable": str(executable), "version": current, "owned": True}
    if not removable:
        return {"type": "completed", "components": [], "retained": [
            {"label": "Rhino Bridge", "reason": "componentOwnershipUnconfirmed"},
        ] if current is not None else []}
    component = {"id": BRIDGE_PACKAGE, "label": "Rhino Bridge", "version": current}
    if step == "uninstall":
        _yak(executable, "uninstall", BRIDGE_PACKAGE)
        if _bridge_version(executable) is not None:
            raise ValueError("bridgeUninstallUnconfirmed")
        return {"type": "completed", "removed": [component]}
    return {"type": "completed", "components": [component]}


def main():
    request = json.loads(sys.stdin.readline())
    executable = installed_rhino()
    step = request["stepId"]
    binding = Path(os.environ["PLUGIN_DATA"]) / "bridge.json"
    try:
        if step in {"uninstall-preview", "uninstall"}:
            result = remove_bridge(step, executable, binding)
        elif executable is None:
            result = {"type": "failed", "reason": "rhinoNotInstalled", "message": "未检测到兼容的 Rhino 8"}
        elif step == "prepare":
            result = prepare_bridge(request, executable, binding)
        else:
            from rhino_mcp._connector_process import running_instance

            result = diagnose_bridge(
                rhino_running=running_instance("Rhino.exe") is not None,
                bridge_ready=False,
            )
    except (OSError, ValueError, subprocess.SubprocessError):
        result = {"type": "failed", "reason": "bridgeComponentUnconfirmed", "message": "Yak 未能确认 Bridge 操作结果，请检查 Rhino 安装后重试"}
    print(json.dumps({**{key: request[key] for key in (
        "protocolVersion", "operationId", "executionId"
    )}, **result}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
