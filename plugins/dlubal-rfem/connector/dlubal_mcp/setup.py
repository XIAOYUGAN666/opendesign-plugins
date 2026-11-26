"""Signed preparation and connection diagnosis for the local RFEM installation."""

import json
import os
import sys
from contextlib import redirect_stdout
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import httpx

from dlubal_mcp.sdk import (
    activate_sdk,
    build_parts,
    prepare_sdk,
    read_binding,
    service_version_matches,
)


def installed_rfem() -> tuple[tuple[Path, str], ...]:
    import winreg

    found = []
    try:
        registry = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\DLUBAL\RFEM",
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


def prepare(data: Path) -> dict:
    installations = installed_rfem()
    if not installations:
        return {
            "type": "failed",
            "reason": "rfemNotInstalled",
            "message": "请先安装 RFEM 6。",
        }
    executable, build = max(
        installations, key=lambda installation: build_parts(installation[1])
    )
    try:
        from dlubal_mcp.rfem.execution import ExecutionManager

        journal = data / "executions.sqlite3"
        if journal.is_file() and ExecutionManager(journal).unfinished():
            return _execution_pending()
        sdk = prepare_sdk(data, executable, build)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return {
                "type": "failed",
                "reason": "rfemSdkUnavailable",
                "message": f"PyPI 未提供 RFEM {build} 的匹配 SDK。",
            }
        return {
            "type": "failed",
            "reason": "rfemSdkDownloadFailed",
            "message": "无法从 PyPI 下载 RFEM SDK。",
        }
    except httpx.HTTPError:
        return {
            "type": "failed",
            "reason": "rfemSdkDownloadFailed",
            "message": "无法从 PyPI 下载 RFEM SDK。",
        }
    except (OSError, ValueError, TypeError, KeyError, PackageNotFoundError):
        return {
            "type": "failed",
            "reason": "rfemSdkPreparationFailed",
            "message": f"无法验证或准备 RFEM {build} 的 SDK。",
        }
    return {"type": "completed", "build": build, "sdkVersion": sdk}


def bound_installation(data: Path) -> dict | None:
    binding = read_binding(data)
    if binding is None:
        return None
    try:
        executable = Path(binding["executable"])
        if executable.is_file() and executable_build(executable) == binding["build"]:
            return binding
    except OSError:
        pass
    return None


def _diagnose_rpc_failure(error: BaseException) -> dict:
    import re

    import grpc

    # The SDK wraps Service authentication errors in RuntimeError with `from None`.
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, grpc.RpcError):
            status = error.code()
            details = (error.details() or "").casefold()
            if status == grpc.StatusCode.UNAUTHENTICATED:
                if details.startswith("api subscription:") and re.search(
                    r"\bused all [\d,]+ of (?:your )?[\d,]+ requests\b", details
                ):
                    return {
                        "type": "failed",
                        "reason": "quotaExceeded",
                        "message": "Dlubal API 本月请求配额已用完。请等待配额重置，或在 Dlubal Extranet 调整订阅。",
                    }
                if details.startswith("api access has been restricted"):
                    return {
                        "type": "failed",
                        "reason": "apiAccessRestricted",
                        "message": "Dlubal API 访问受限。请在 Dlubal Extranet 检查公司授权及剩余请求配额。",
                    }
            if status in (
                grpc.StatusCode.UNAUTHENTICATED,
                grpc.StatusCode.PERMISSION_DENIED,
            ):
                return {
                    "type": "failed",
                    "reason": "authenticationFailed",
                    "message": "RFEM API 拒绝访问，请检查已保存的 API Key 和账户授权。",
                }
            break
        cause = error.__cause__ or error.__context__
        if cause is None:
            break
        error = cause
    return {
        "type": "failed",
        "reason": "connectionFailed",
        "message": "RFEM API Service 连接未通过验证。",
    }


def diagnose(data: Path, key: str) -> dict:
    from dlubal_mcp.core.product_execution import read_application_info
    from dlubal_mcp.core.products import api_port_open
    from dlubal_mcp.plugin_stdio import read_connection_settings
    from dlubal_mcp.rfem.session import RFEMSession

    binding = bound_installation(data)
    if binding is None:
        return {
            "type": "failed",
            "reason": "softwarePreparationRequired",
            "message": "RFEM 安装或 SDK 已变化，请卸载后重新安装插件。",
        }
    if not key:
        return {
            "type": "failed",
            "reason": "credentialMissing",
            "message": "请在插件设置中填写 RFEM API Key。",
        }
    connection = read_connection_settings()
    if not api_port_open(connection.address, connection.port):
        return {
            "type": "failed",
            "reason": "apiUnreachable",
            "message": "RFEM API 端口不可达。请确认 RFEM 已启动并启用 API 服务。",
        }
    activate_sdk(data)
    try:
        result = read_application_info(
            RFEMSession("", key, connection.address, connection.port), "rfem"
        )
    except Exception as error:  # noqa: BLE001 - project only confirmed RPC categories
        return _diagnose_rpc_failure(error)
    if not service_version_matches(binding["build"], result["version"]):
        return {
            "type": "failed",
            "reason": "unsupportedSoftwareVersion",
            "message": "当前端点未通过已准备 SDK 的 RFEM 版本核对。",
        }
    return {
        "type": "completed",
        "message": f"已连接 RFEM {result['version']}，dlubal.api {binding['sdkVersion']}。",
    }


def _execution_pending() -> dict:
    return {
        "type": "failed",
        "reason": "rfemExecutionUnresolved",
        "message": "RFEM 有正在运行或结果未确认的执行，请先查询原执行 ID。",
    }


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
    data = Path(os.environ["PLUGIN_DATA"])
    if step == "prepare":
        result = prepare(data)
    elif step == "diagnose":
        with redirect_stdout(sys.stderr):
            result = diagnose(data, _read_credential())
    elif step in {"uninstall-preview", "uninstall"}:
        from dlubal_mcp.rfem.execution import ExecutionManager

        journal = data / "executions.sqlite3"
        result = (
            _execution_pending()
            if journal.is_file() and ExecutionManager(journal).unfinished()
            else {"type": "completed", "components": []}
        )
    else:
        result = {"type": "failed", "reason": "unsupportedSetupStep"}
    print(
        json.dumps(
            {
                **{
                    key: request[key]
                    for key in ("protocolVersion", "operationId", "executionId")
                },
                **result,
            },
            ensure_ascii=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
