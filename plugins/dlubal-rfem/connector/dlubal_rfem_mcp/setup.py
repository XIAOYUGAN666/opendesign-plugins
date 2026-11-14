"""Package-owned matching of RFEM installations to reviewed SDK versions."""

import hashlib
import json
import os
import sys
from pathlib import Path

_SDK_BY_BUILD = {"6.14.0013.249": "2.14.13"}


def select_sdk(build: str) -> str | None:
    return _SDK_BY_BUILD.get(build)


def installed_rfem() -> tuple[tuple[Path, str], ...]:
    import winreg

    found = []
    try:
        registry = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\DLUBAL\RFEM",
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
    except FileNotFoundError:
        return ()
    with registry:
        count, _, _ = winreg.QueryInfoKey(registry)
        for index in range(count):
            name = winreg.EnumKey(registry, index)
            if not name.startswith("6."):
                continue
            with winreg.OpenKey(registry, name) as item:
                try:
                    directory, _ = winreg.QueryValueEx(item, "InstallDirectory")
                    version, _ = winreg.QueryValueEx(item, "Version")
                except FileNotFoundError:
                    continue
                executable = Path(directory) / "bin" / "RFEM6.exe"
                if executable.is_file() and isinstance(version, str):
                    found.append((executable.resolve(), executable_build(executable)))
    return tuple(found)


def executable_build(executable: Path) -> str:
    import win32api

    info = win32api.GetFileVersionInfo(str(executable), "\\")
    ms, ls = info["FileVersionMS"], info["FileVersionLS"]
    return f"{ms >> 16}.{ms & 65535}.{ls >> 16:04}.{ls & 65535}"


def prepared_build(data: Path) -> str | None:
    from importlib.metadata import version

    try:
        binding = json.loads((data / "rfem-binding.json").read_bytes())
        build = binding["build"]
        executable = Path(binding["executable"])
        sdk = version("dlubal-api")
        if select_sdk(build) == binding["sdkVersion"] == sdk and executable_build(executable) == build:
            return build
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def prepare(request: dict) -> dict:
    installations = installed_rfem()
    options = {
        hashlib.sha256(str(executable).encode()).hexdigest(): (executable, build, sdk)
        for executable, build in installations if (sdk := select_sdk(build))
    }
    if not options:
        return {
            "type": "failed", "reason": "rfemCompatibleBuildUnavailable",
            "message": "没有匹配的 RFEM 构建。检测结果：" + (", ".join(build for _, build in installations) or "未安装"),
        }
    selected = request.get("optionId")
    if selected is None and len(options) > 1:
        return {"type": "selection", "selectionRequestId": "rfem-installation", "options": [
            {"id": key, "label": f"RFEM {build} · dlubal.api {sdk}"}
            for key, (_, build, sdk) in options.items()
        ]}
    match = options.get(selected) if selected is not None else next(iter(options.values()))
    if match is None:
        return {"type": "failed", "reason": "rfemInstallationChanged", "message": "所选 RFEM 实例已变化，请重新检测"}
    _, build, sdk = match
    from importlib.metadata import version

    if version("dlubal-api") != sdk:
        return {"type": "failed", "reason": "rfemSdkMismatch", "message": f"RFEM {build} 需要 dlubal.api {sdk}"}
    data = Path(os.environ["PLUGIN_DATA"])
    data.mkdir(parents=True, exist_ok=True)
    pending = data / "rfem-binding.tmp"
    pending.write_text(json.dumps({
        "build": build, "sdkVersion": sdk, "executable": str(match[0]),
    }), encoding="utf-8")
    pending.replace(data / "rfem-binding.json")
    return {"type": "completed", "build": build, "sdkVersion": sdk}


def diagnose(data: Path, key: str) -> dict:
    from importlib.metadata import version

    from dlubal_rfem_mcp.rfem.config import RFEMConfig
    from dlubal_rfem_mcp.rfem.session import RFEMSession, restriction_category

    build = prepared_build(data)
    if build is None:
        return {"type": "failed", "reason": "softwarePreparationRequired", "message": "RFEM 安装或 SDK 绑定已变化，请在插件页重新准备组件。"}
    if not key:
        return {"type": "failed", "reason": "credentialMissing", "message": "请在插件设置中填写 RFEM API Key。"}
    config = RFEMConfig()
    session = RFEMSession(key, config.url, config.api_port, expected_build=build)
    try:
        with session.connection(service_query=True) as app:
            session.validate_application_info(app.get_application_info())
    except Exception as error:
        reason = session.restriction or restriction_category(error)
        message = {
            "authenticationFailed": "RFEM 拒绝了已保存的 API Key，请在插件设置中更新。",
            "authorizationRestricted": "RFEM 未授权此 API Key 访问服务。",
            "quotaRestricted": "RFEM API 配额受限，请检查 Dlubal 账户。",
            "apiAccessRestricted": "RFEM API 服务拒绝访问，请检查 Dlubal 账户授权。",
            "unsupportedSoftwareVersion": "当前运行的 RFEM 版本与已准备的版本不一致。",
        }.get(reason, "无法连接 RFEM API。请确认 RFEM 已启动并启用 API 服务。")
        return {"type": "failed", "reason": reason, "message": message}
    return {"type": "completed", "message": f"已连接 RFEM {build}，dlubal.api {version('dlubal-api')}，Service 查询成功。"}


def _read_credential() -> str:
    import msvcrt

    if len(sys.argv) != 3 or sys.argv[1] != "--credential-handle":
        return ""
    descriptor = msvcrt.open_osfhandle(int(sys.argv[2]), os.O_RDONLY | os.O_BINARY)
    with os.fdopen(descriptor, "rb") as stream:
        value = stream.read(65537)
    if len(value) > 65536:
        raise ValueError("credentialTooLarge")
    return value.decode("utf-8")


def main():
    request = json.loads(sys.stdin.readline())
    step = request["stepId"]
    if step == "prepare":
        result = prepare(request)
    elif step == "diagnose":
        result = diagnose(Path(os.environ["PLUGIN_DATA"]), _read_credential())
    elif step in {"uninstall-preview", "uninstall"}:
        result = {"type": "completed", "components": []}
    else:
        result = {"type": "failed", "reason": "unsupportedSetupStep"}
    print(json.dumps({**{key: request[key] for key in (
        "protocolVersion", "operationId", "executionId"
    )}, **result}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
