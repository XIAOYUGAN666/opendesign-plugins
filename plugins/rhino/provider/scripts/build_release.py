"""Build the deterministic, private Rhino Provider + Yak release archive."""

from __future__ import annotations

import argparse
import datetime as dt
import email.parser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from xml.etree import ElementTree
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_probe import ReleaseProbeError, probe_managed_runtime


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PROJECT_ROOT.parent
NATIVE_ROOT = PLUGIN_ROOT / "native"
RUNTIME_LOCK_PATH = PROJECT_ROOT / "packaging" / "runtime-lock.json"
RELEASE_TEMPLATE_PATH = PROJECT_ROOT / "packaging" / "release-template.json"
PLUGIN_GUID = "74a1e8f4-1134-4686-a3e5-ee8bf393dec9"
YAK_NAME = "rhino-mcp-bridge-0.1.2-rh8-win.yak"
ARCHIVE_NAME = "rhino-0.1.2-windows-x86_64.zip"
BUILD_ID_SENTINEL = 'PROVIDER_BUILD_ID = "sha256:" + "0" * 64'
BUILD_ID_PATTERN = re.compile(r"^PROVIDER_BUILD_ID\s*=.*$", re.MULTILINE)
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

EMBEDDED_RUNTIME_FILES = frozenset(
    {
        "python.exe",
        "python312.dll",
        "python3.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "pyexpat.pyd",
        "select.pyd",
        "unicodedata.pyd",
        "winsound.pyd",
        "_asyncio.pyd",
        "_bz2.pyd",
        "_ctypes.pyd",
        "_decimal.pyd",
        "_elementtree.pyd",
        "_hashlib.pyd",
        "_lzma.pyd",
        "_msi.pyd",
        "_multiprocessing.pyd",
        "_overlapped.pyd",
        "_queue.pyd",
        "_socket.pyd",
        "_sqlite3.pyd",
        "_ssl.pyd",
        "_uuid.pyd",
        "_wmi.pyd",
        "_zoneinfo.pyd",
        "libcrypto-3.dll",
        "libffi-8.dll",
        "libssl-3.dll",
        "sqlite3.dll",
        "python312.zip",
        "python312._pth",
        "LICENSE.txt",
        "python.cat",
    }
)


class ReleaseBuildError(RuntimeError):
    """A release input failed closed validation or a build step failed."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _frozen_tool_surface(value: Any) -> list[dict[str, Any]]:
    fields = ("name", "description", "inputSchema", "annotations")
    if not isinstance(value, list):
        raise ReleaseBuildError("The frozen MCP tool catalog must be an array.")

    projected: list[dict[str, Any]] = []
    for tool in value:
        if not isinstance(tool, dict) or not all(field in tool for field in fields):
            raise ReleaseBuildError(
                "The MCP tool catalog has an invalid frozen surface."
            )
        item = {field: tool[field] for field in fields}
        annotations = item["annotations"]
        if isinstance(annotations, dict) and "title" not in annotations:
            item["annotations"] = {"title": None, **annotations}
        projected.append(item)
    return projected


def embedded_pth_content() -> str:
    """Pin the embeddable interpreter to its runtime and release-root paths."""
    return "python312.zip\n.\nLib\\site-packages\nimport site\n"


def managed_bootstrap_command(runtime_path: str) -> tuple[str, ...]:
    return (runtime_path, "-I", "-s", "-B", "-m", "rhino_mcp.managed_bootstrap")


def _safe_member_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise ReleaseBuildError(
            "Release member name is not a safe POSIX relative path."
        )
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ReleaseBuildError("Release member escapes its archive root.")
    if path.as_posix() != name:
        raise ReleaseBuildError("Release member is not in normalized POSIX form.")
    return name


def deterministic_zip_bytes(
    files: Mapping[str, bytes], source_date_epoch: int
) -> bytes:
    """Serialize file members in a stable order with normalized ZIP metadata."""
    if not isinstance(source_date_epoch, int) or isinstance(source_date_epoch, bool):
        raise ReleaseBuildError("SOURCE_DATE_EPOCH must be an integer.")
    timestamp = dt.datetime.fromtimestamp(source_date_epoch, tz=dt.timezone.utc)
    timestamp = timestamp.replace(
        second=timestamp.second - timestamp.second % 2, microsecond=0
    )
    timestamp = timestamp.replace(year=max(1980, min(2107, timestamp.year)))
    archive_timestamp = (
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second,
    )

    normalized: dict[str, bytes] = {}
    for raw_name, payload in files.items():
        name = _safe_member_name(raw_name)
        if name in normalized:
            raise ReleaseBuildError("Release archive contains a duplicate member.")
        if not isinstance(payload, bytes):
            raise ReleaseBuildError("Release members must be supplied as bytes.")
        normalized[name] = payload

    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name in sorted(normalized):
            info = zipfile.ZipInfo(name, date_time=archive_timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            info.flag_bits = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(
                info,
                normalized[name],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return output.getvalue()


def build_yak_archive_bytes(
    entries: Mapping[str, bytes], source_date_epoch: int
) -> bytes:
    return deterministic_zip_bytes(entries, source_date_epoch)


def expected_release_members(
    provider_files: set[str], contract_files: set[str]
) -> set[str]:
    return {
        "manifest.json",
        "file-manifest.json",
        "SBOM.spdx.json",
        "runtime/python/python.exe",
        f"native/{YAK_NAME}",
        *provider_files,
        *contract_files,
    }


def native_artifact_path(
    artifacts_root: Path,
    target_framework: str,
    configuration: str = "Release",
) -> Path:
    if target_framework not in {"net48", "net8.0"}:
        raise ReleaseBuildError(
            "Only the approved Rhino 8 target frameworks are valid."
        )
    if not configuration or not configuration.isascii() or not configuration.isalnum():
        raise ReleaseBuildError("The .NET build configuration is invalid.")
    # .NET 8 artifacts output uses lowercase configuration_TFM pivots.
    return (
        artifacts_root
        / "bin"
        / "RhinoMcpBridge"
        / f"{configuration.lower()}_{target_framework}"
    )


def native_capability_probe_path(artifacts_root: Path) -> Path:
    return artifacts_root / "bin" / "BridgeTestHost" / "release" / "BridgeTestHost.dll"


def native_path_map(workspace: Path) -> str:
    """Replace machine-specific checkout and temporary build paths in release symbols."""
    mappings = (
        (PLUGIN_ROOT.resolve(strict=True), "/_/rhino"),
        (workspace.resolve(strict=True), "/_/rhino-build"),
    )
    ordered = sorted(mappings, key=lambda item: len(str(item[0])), reverse=True)
    # MSBuild also treats commas on -p as property-list separators; %2C keeps
    # the PathMap's own comma-separated mappings intact until property parsing.
    return "%2C".join(f"{physical}={virtual}" for physical, virtual in ordered)


def native_package_files(
    output_directory: Path, target_framework: str
) -> dict[str, bytes]:
    if target_framework not in {"net48", "net8.0"}:
        raise ReleaseBuildError(
            "Only the approved Rhino 8 target frameworks may enter Yak."
        )
    if any(
        path.name.casefold() in {"rhinocommon.dll", "grasshopper.dll", "rhino.ui.dll"}
        or (
            path.name.casefold().startswith("eto.") and path.suffix.casefold() == ".dll"
        )
        for path in output_directory.iterdir()
        if path.is_file()
    ):
        raise ReleaseBuildError(
            "The Yak payload must not include software-owned assemblies."
        )
    files = {
        PurePosixPath(target_framework, path.name).as_posix(): path.read_bytes()
        for path in sorted(output_directory.iterdir())
        if path.is_file() and path.suffix.lower() in {".rhp", ".dll"}
    }
    if f"{target_framework}/RhinoMcpBridge.rhp" not in files:
        raise ReleaseBuildError(
            f"The native {target_framework} plug-in assembly is missing."
        )
    if not any(
        name.startswith(f"{target_framework}/") and name.endswith(".dll")
        for name in files
    ):
        raise ReleaseBuildError(
            f"The native {target_framework} dependencies are missing."
        )
    return files


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(
            f"Could not read release JSON input: {path.name}."
        ) from exc
    if not isinstance(value, dict):
        raise ReleaseBuildError(f"Release JSON input must be an object: {path.name}.")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configure_plugin_root(root: Path) -> None:
    """Select an independent plugin checkout, without consulting a host tree."""
    global \
        PLUGIN_ROOT, \
        PROJECT_ROOT, \
        NATIVE_ROOT, \
        RUNTIME_LOCK_PATH, \
        RELEASE_TEMPLATE_PATH
    root = root.resolve()
    if (
        not (root / "provider" / "pyproject.toml").is_file()
        or not (root / "native").is_dir()
    ):
        raise ReleaseBuildError("The plugin root has an invalid build layout.")
    PLUGIN_ROOT = root
    PROJECT_ROOT = root / "provider"
    NATIVE_ROOT = root / "native"
    RUNTIME_LOCK_PATH = PROJECT_ROOT / "packaging" / "runtime-lock.json"
    RELEASE_TEMPLATE_PATH = PROJECT_ROOT / "packaging" / "release-template.json"


def _runtime_lock() -> dict[str, Any]:
    lock = _read_json(RUNTIME_LOCK_PATH)
    python = lock.get("python")
    if not isinstance(python, dict):
        raise ReleaseBuildError("The Python runtime lock has no Python identity.")
    url = python.get("url")
    parsed = urllib.parse.urlparse(url if isinstance(url, str) else "")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.python.org"
        or python.get("version") != "3.12.10"
        or python.get("architecture") != "amd64"
        or not isinstance(python.get("byteLength"), int)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(python.get("sha256", "")))
    ):
        raise ReleaseBuildError("The locked Python runtime identity is invalid.")
    return lock


def _verify_runtime_archive(path: Path, lock: dict[str, Any]) -> None:
    expected = lock["python"]
    if not path.is_file():
        raise ReleaseBuildError("The official Python embeddable archive is missing.")
    if (
        path.stat().st_size != expected["byteLength"]
        or sha256_digest(path.read_bytes()) != expected["sha256"]
    ):
        raise ReleaseBuildError(
            "The Python embeddable archive did not match its official locked digest."
        )


def _download_runtime_archive(lock: dict[str, Any], supplied_path: Path | None) -> Path:
    if supplied_path is not None:
        resolved = supplied_path.resolve(strict=True)
        _verify_runtime_archive(resolved, lock)
        return resolved

    runtime = lock["python"]
    cache_root = (
        Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
        / "rhino-mcp-release-cache"
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    archive_path = cache_root / "python-3.12.10-embed-amd64.zip"
    try:
        _verify_runtime_archive(archive_path, lock)
        return archive_path
    except ReleaseBuildError:
        pass

    temporary_path = cache_root / f"{archive_path.name}.download"
    request = urllib.request.Request(
        runtime["url"],
        headers={"User-Agent": "Rhino-Provider-Release-Builder/1"},
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            temporary_path.open("wb") as output,
        ):
            if response.status != 200:
                raise ReleaseBuildError("The official Python runtime download failed.")
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        _verify_runtime_archive(temporary_path, lock)
        os.replace(temporary_path, archive_path)
    except ReleaseBuildError:
        raise
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        raise ReleaseBuildError(
            "Could not retrieve the official Python runtime archive."
        ) from exc
    return archive_path


def _safe_ascii_temp_root() -> Path:
    configured = os.environ.get("RHINO_BUILD_TEMP")
    candidates = [Path(configured)] if configured else []
    candidates.extend((Path(r"C:\Windows\Temp"), Path(tempfile.gettempdir())))
    for candidate in candidates:
        if not str(candidate).isascii() or not candidate.is_dir():
            continue
        try:
            with tempfile.TemporaryDirectory(
                prefix="rhino-release-check-", dir=candidate
            ):
                return candidate
        except OSError:
            continue
    raise ReleaseBuildError(
        "Set RHINO_BUILD_TEMP to an existing writable ASCII-only temporary directory."
    )


def _resolve_tool(environment_key: str, executable: str, label: str) -> str:
    configured = os.environ.get(environment_key)
    selected = configured or shutil.which(executable)
    if not selected or not Path(selected).is_file():
        raise ReleaseBuildError(f"Set {environment_key} or add {label} to PATH.")
    return str(Path(selected).resolve())


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        raise ReleaseBuildError("A release build command failed.")
    return output


def _dotnet_executable() -> str:
    executable = _resolve_tool("RHINO_DOTNET_EXE", "dotnet", ".NET SDK 8 or later")
    version = _run([executable, "--version"], cwd=PLUGIN_ROOT).splitlines()[-1]
    match = re.match(r"(\d+)\.", version)
    if not match or int(match.group(1)) < 8:
        raise ReleaseBuildError("The native release requires the .NET 8 SDK or later.")
    return executable


def _uv_executable() -> str:
    return _resolve_tool("RHINO_UV_EXE", "uv", "uv")


def _extract_python_runtime(
    archive_path: Path, runtime_root: Path, lock: dict[str, Any]
) -> None:
    expected_members = EMBEDDED_RUNTIME_FILES | {"pythonw.exe"}
    runtime_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if set(names) != expected_members or len(names) != len(expected_members):
                raise ReleaseBuildError(
                    "The locked Python runtime archive has an unexpected member set."
                )
            if any(
                "/" in name or "\\" in name or name in (".", "..") for name in names
            ):
                raise ReleaseBuildError(
                    "The Python embeddable archive contains a non-flat member."
                )
            for info in infos:
                if info.filename == "pythonw.exe":
                    continue
                target = runtime_root / info.filename
                target.write_bytes(archive.read(info))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseBuildError(
            "The Python embeddable archive could not be extracted safely."
        ) from exc
    (runtime_root / "python312._pth").write_text(
        embedded_pth_content(), encoding="ascii", newline="\n"
    )
    (runtime_root / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    _verify_runtime_archive(archive_path, lock)


def _install_runtime_dependencies(stage_root: Path, uv_exe: str) -> None:
    requirements = stage_root.parent / "runtime-requirements.txt"
    site_packages = stage_root / "runtime" / "python" / "Lib" / "site-packages"
    export_command = [
        uv_exe,
        "export",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--format",
        "requirements.txt",
        "--output-file",
        str(requirements),
    ]
    _run(export_command, cwd=PROJECT_ROOT)
    install_command = [
        uv_exe,
        "pip",
        "install",
        "--python-version",
        "3.12",
        "--python-platform",
        "x86_64-pc-windows-msvc",
        "--only-binary",
        ":all:",
        "--no-deps",
        "--require-hashes",
        "--target",
        str(site_packages),
        "-r",
        str(requirements),
    ]
    _run(install_command, cwd=PROJECT_ROOT)


def _build_native_outputs(
    workspace: Path,
    dotnet_exe: str,
    provider_build_id: str,
) -> dict[str, Path]:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", provider_build_id):
        raise ReleaseBuildError("The precomputed Provider build identity is invalid.")
    artifacts = workspace / "dotnet-artifacts"
    project = NATIVE_ROOT / "RhinoMcpBridge.csproj"
    _run(
        [
            dotnet_exe,
            "restore",
            str(project),
            "--locked-mode",
            "--artifacts-path",
            str(artifacts),
        ],
        cwd=PLUGIN_ROOT,
    )
    _run(
        [
            dotnet_exe,
            "build",
            str(project),
            "--configuration",
            "Release",
            "--no-restore",
            "--artifacts-path",
            str(artifacts),
            "-p:ContinuousIntegrationBuild=true",
            "-p:Deterministic=true",
            f"-p:RhinoProviderBuildId={provider_build_id}",
            f"-p:PathMap={native_path_map(workspace)}",
        ],
        cwd=PLUGIN_ROOT,
    )
    outputs = {
        target: native_artifact_path(artifacts, target)
        for target in ("net48", "net8.0")
    }
    for target, directory in outputs.items():
        native_package_files(directory, target)
    return outputs


def _build_native_capability_probe(
    workspace: Path,
    dotnet_exe: str,
    provider_build_id: str,
) -> Path:
    artifacts = workspace / "dotnet-artifacts"
    project = NATIVE_ROOT / "tests" / "BridgeTestHost" / "BridgeTestHost.csproj"
    _run(
        [
            dotnet_exe,
            "restore",
            str(project),
            "--locked-mode",
            "--artifacts-path",
            str(artifacts),
        ],
        cwd=PLUGIN_ROOT,
    )
    _run(
        [
            dotnet_exe,
            "build",
            str(project),
            "--configuration",
            "Release",
            "--no-restore",
            "--artifacts-path",
            str(artifacts),
            "-p:ContinuousIntegrationBuild=true",
            "-p:Deterministic=true",
            f"-p:RhinoProviderBuildId={provider_build_id}",
            f"-p:PathMap={native_path_map(workspace)}",
        ],
        cwd=PLUGIN_ROOT,
    )
    probe = native_capability_probe_path(artifacts)
    if not probe.is_file():
        raise ReleaseBuildError("The native capability probe was not built.")
    return probe


def _native_capability_digest(
    native_outputs: Mapping[str, Path],
    dotnet_exe: str,
    probe: Path,
) -> str:
    if set(native_outputs) != {"net48", "net8.0"} or not probe.is_file():
        raise ReleaseBuildError(
            "Both compiled native targets and the capability probe are required."
        )

    digests: list[str] = []
    for target in ("net48", "net8.0"):
        assembly = native_outputs[target] / "RhinoMcpBridge.rhp"
        if not assembly.is_file():
            raise ReleaseBuildError(
                f"The compiled native {target} assembly is missing."
            )
        output = _run(
            [
                dotnet_exe,
                str(probe.resolve()),
                "--capability-digest",
                str(assembly.resolve()),
            ],
            cwd=PLUGIN_ROOT,
        )
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if len(lines) != 1 or not SHA256_PATTERN.fullmatch(lines[0]):
            raise ReleaseBuildError(
                f"The compiled native {target} capability digest is invalid."
            )
        digests.append(lines[0])

    if digests[0] != digests[1]:
        raise ReleaseBuildError(
            "The Rhino native target frameworks disagree on capability digest."
        )
    return digests[0]


def _copy_provider_source(stage_root: Path) -> set[str]:
    source = PROJECT_ROOT / "rhino_mcp"
    target = stage_root / "runtime/python/Lib/site-packages/rhino_mcp"
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    return {
        PurePosixPath(
            "runtime/python/Lib/site-packages/rhino_mcp",
            path.relative_to(target).as_posix(),
        ).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    }


def _copy_contracts(stage_root: Path) -> set[str]:
    source = PLUGIN_ROOT / "contracts"
    target = stage_root / "contracts"
    shutil.copytree(source, target)
    names = {
        PurePosixPath("contracts", path.relative_to(target).as_posix()).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    }
    if not names or any(not name.endswith(".json") for name in names):
        raise ReleaseBuildError(
            "The Provider contract tree contains an unexpected file type."
        )
    return names


def _source_records() -> list[dict[str, str]]:
    source_root = PROJECT_ROOT / "rhino_mcp"
    records: list[dict[str, str]] = []
    for path in sorted(source_root.rglob("*")):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in (".pyc", ".pyo")
        ):
            continue
        raw = path.read_bytes()
        if path.name == "build_identity.py":
            text = raw.decode("utf-8")
            text, replacements = BUILD_ID_PATTERN.subn(
                'PROVIDER_BUILD_ID = "<provider-build-id>"', text
            )
            if replacements != 1:
                raise ReleaseBuildError(
                    "The Provider build identity source is not the expected template."
                )
            raw = text.encode("utf-8")
        records.append(
            {
                "path": PurePosixPath(
                    "rhino_mcp", path.relative_to(source_root).as_posix()
                ).as_posix(),
                "sha256": sha256_digest(raw),
            }
        )
    return records


def _contract_records() -> list[dict[str, str]]:
    source_root = PLUGIN_ROOT / "contracts"
    return [
        {
            "path": PurePosixPath(
                "contracts", path.relative_to(source_root).as_posix()
            ).as_posix(),
            "sha256": sha256_digest(path.read_bytes()),
        }
        for path in sorted(source_root.rglob("*.json"))
        if path.name not in ("provider-build.json", "tool-catalog.json")
    ]


def _native_input_records() -> list[dict[str, str]]:
    files = [
        NATIVE_ROOT / "RhinoMcpBridge.csproj",
        NATIVE_ROOT / "packages.lock.json",
        NATIVE_ROOT / "package" / "manifest.yml",
        *(
            path
            for path in NATIVE_ROOT.rglob("*.cs")
            if "tests" not in path.relative_to(NATIVE_ROOT).parts
            and "obj" not in path.relative_to(NATIVE_ROOT).parts
            and "bin" not in path.relative_to(NATIVE_ROOT).parts
        ),
    ]
    records: list[dict[str, str]] = []
    for path in sorted(set(files)):
        if not path.is_file():
            raise ReleaseBuildError(
                f"Required native build input is missing: {path.name}."
            )
        records.append(
            {
                "path": PurePosixPath(
                    "native", path.relative_to(NATIVE_ROOT).as_posix()
                ).as_posix(),
                "sha256": sha256_digest(path.read_bytes()),
            }
        )
    return records


def _build_inputs() -> list[dict[str, str]]:
    paths = (
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "uv.lock",
        PLUGIN_ROOT / "global.json",
        RUNTIME_LOCK_PATH,
        NATIVE_ROOT / "RhinoMcpBridge.csproj",
        NATIVE_ROOT / "packages.lock.json",
        NATIVE_ROOT / "package" / "manifest.yml",
    )
    inputs: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            raise ReleaseBuildError(
                f"Required release build input is missing: {path.name}."
            )
        inputs.append(
            {
                "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                "sha256": sha256_digest(path.read_bytes()),
            }
        )
    return inputs


def _native_output_records(
    outputs: dict[str, Path],
) -> tuple[list[dict[str, str]], dict[str, bytes]]:
    records: list[dict[str, str]] = []
    yak_entries: dict[str, bytes] = {
        "manifest.yml": (NATIVE_ROOT / "package" / "manifest.yml").read_bytes()
    }
    for target, directory in outputs.items():
        for member, payload in native_package_files(directory, target).items():
            yak_entries[member] = payload
            records.append({"path": member, "sha256": sha256_digest(payload)})
    return records, yak_entries


def validate_root_identity() -> dict[str, Any]:
    """Reject stale generated identity copies before any release is built."""
    identity = _read_json(PLUGIN_ROOT / "plugin.json")
    version = identity.get("version")
    if identity.get("name") != "rhino" or not isinstance(version, str):
        raise ReleaseBuildError("The root plugin identity is invalid.")
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    template = _read_json(RELEASE_TEMPLATE_PATH)
    bridge_project = ElementTree.parse(NATIVE_ROOT / "RhinoMcpBridge.csproj").getroot()
    bridge_versions = {
        node.tag: node.text
        for node in bridge_project.iter()
        if node.tag
        in {"Version", "AssemblyVersion", "FileVersion", "InformationalVersion"}
    }
    provider_identity = (PROJECT_ROOT / "rhino_mcp" / "build_identity.py").read_text(
        encoding="utf-8"
    )
    assembly_identity = (NATIVE_ROOT / "Properties" / "AssemblyInfo.cs").read_text(
        encoding="utf-8"
    )
    yak_identity = (NATIVE_ROOT / "package" / "manifest.yml").read_text(
        encoding="utf-8"
    )
    if (
        project["project"]["version"] != version
        or template["pluginVersion"] != version
        or template["native"]["version"] != version
        or bridge_versions
        != {
            "Version": version,
            "AssemblyVersion": version + ".0",
            "FileVersion": version + ".0",
            "InformationalVersion": version,
        }
        or f'RELEASE_VERSION = "{version}"' not in provider_identity
        or f'AssemblyVersion("{version}.0")' not in assembly_identity
        or f'AssemblyFileVersion("{version}.0")' not in assembly_identity
        or f'AssemblyInformationalVersion("{version}")' not in assembly_identity
        or not re.search(
            r"(?m)^version:\s*" + re.escape(version) + r"\s*$", yak_identity
        )
    ):
        raise ReleaseBuildError(
            "The derived release identity differs from plugin.json."
        )
    return identity


def _build_identity(
    lock: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    validate_root_identity()
    description = {
        "schemaVersion": 1,
        "packageId": "rhino",
        "releaseVersion": "0.1.2",
        "providerSource": _source_records(),
        "contractSource": _contract_records(),
        "buildInputs": _build_inputs(),
        "pythonRuntime": {
            "version": lock["python"]["version"],
            "architecture": lock["python"]["architecture"],
            "byteLength": lock["python"]["byteLength"],
            "sha256": lock["python"]["sha256"],
        },
        "native": {
            "targetFrameworks": ["net48", "net8.0"],
            "pluginId": PLUGIN_GUID,
            "inputs": _native_input_records(),
        },
    }
    identity = sha256_digest(canonical_json_bytes(description))
    return description, identity


def _patch_build_identity(stage_root: Path, identity: str) -> None:
    path = stage_root / "runtime/python/Lib/site-packages/rhino_mcp/build_identity.py"
    content = path.read_text(encoding="utf-8")
    patched, replacements = BUILD_ID_PATTERN.subn(
        f'PROVIDER_BUILD_ID = "{identity}"', content
    )
    if replacements != 1:
        raise ReleaseBuildError(
            "The staged Provider build identity could not be set exactly once."
        )
    path.write_text(patched, encoding="utf-8", newline="\n")


def _sbom_package(
    name: str,
    version: str,
    *,
    license_expression: str = "NOASSERTION",
    checksum: str | None = None,
    purl: str | None = None,
) -> dict[str, Any]:
    package_id = re.sub(r"[^A-Za-z0-9.-]+", "-", f"{name}-{version}")
    package: dict[str, Any] = {
        "name": name,
        "SPDXID": f"SPDXRef-Package-{package_id}",
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": license_expression,
        "copyrightText": "NOASSERTION",
    }
    if checksum:
        package["checksums"] = [{"algorithm": "SHA256", "checksumValue": checksum}]
    if purl:
        package["externalRefs"] = [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": purl,
            }
        ]
    return package


def _sbom(
    stage_root: Path, lock: dict[str, Any], native_yak: bytes, source_date_epoch: int
) -> bytes:
    site_packages = stage_root / "runtime" / "python" / "Lib" / "site-packages"
    packages = [
        _sbom_package("rhino-mcp-provider", "0.1.2"),
        _sbom_package(
            "CPython",
            lock["python"]["version"],
            license_expression="Python-2.0",
            checksum=lock["python"]["sha256"].removeprefix("sha256:"),
        ),
        _sbom_package(
            "rhino-mcp-bridge",
            "0.1.2",
            checksum=hashlib.sha256(native_yak).hexdigest(),
        ),
    ]
    for metadata_path in sorted(site_packages.glob("*.dist-info/METADATA")):
        metadata = email.parser.Parser().parsestr(
            metadata_path.read_text(encoding="utf-8", errors="replace")
        )
        name = metadata.get("Name")
        version = metadata.get("Version")
        if not name or not version:
            raise ReleaseBuildError(
                f"Wheel metadata is incomplete: {metadata_path.parent.name}."
            )
        license_expression = metadata.get("License-Expression") or "NOASSERTION"
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        packages.append(
            _sbom_package(
                name,
                version,
                license_expression=license_expression,
                purl=f"pkg:pypi/{normalized}@{version}",
            )
        )
    packages.sort(
        key=lambda package: (package["name"].casefold(), package["versionInfo"])
    )
    created = dt.datetime.fromtimestamp(source_date_epoch, tz=dt.timezone.utc)
    payload: dict[str, Any] = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "rhino-provider-release-0.1.2",
        "documentNamespace": "https://spdx.org/spdxdocs/rhino-provider-release-0.1.2-"
        + hashlib.sha256(canonical_json_bytes(packages)).hexdigest(),
        "creationInfo": {
            "created": created.replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "creators": ["Tool: Rhino Release Builder"],
        },
        "packages": packages,
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": package["SPDXID"],
                "relationshipType": "DESCRIBES",
            }
            for package in packages
        ],
    }
    return canonical_json_bytes(payload)


def _write_file_manifest(files: dict[str, bytes]) -> bytes:
    manifest = {
        "schemaVersion": 1,
        "files": [
            {
                "path": name,
                "byteLength": len(files[name]),
                "sha256": sha256_digest(files[name]),
            }
            for name in sorted(files)
            if name not in ("manifest.json", "file-manifest.json")
        ],
    }
    return canonical_json_bytes(manifest)


def release_tree_bytes(stage_root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(stage_root.rglob("*")):
        if not path.is_file():
            continue
        member = path.relative_to(stage_root).as_posix()
        folded_parts = tuple(part.casefold() for part in PurePosixPath(member).parts)
        if (
            "__pycache__" in folded_parts
            or "tests" in folded_parts
            or path.suffix.lower() in {".pyc", ".pyo"}
        ):
            continue
        if path.suffix.casefold() == ".exe" and member != "runtime/python/python.exe":
            continue
        if member in files:
            raise ReleaseBuildError("The staged release has duplicate relative paths.")
        files[_safe_member_name(member)] = path.read_bytes()
    return files


def _safe_manifest(template: dict[str, Any]) -> dict[str, Any]:
    manifest = json.loads(json.dumps(template))
    expected_fields = {
        "manifestSchemaVersion",
        "packageId",
        "pluginVersion",
        "releaseSequence",
        "artifactVariantId",
        "platform",
        "hostContract",
        "agentContract",
        "startupEnvelopeVersion",
        "entrypoint",
        "module",
        "components",
        "mcp",
        "software",
        "adapter",
        "providerBuildId",
        "toolCatalogDigest",
        "configurationSchemaVersion",
        "filesDigest",
        "sbomDigest",
        "native",
    }
    if set(manifest) != expected_fields:
        raise ReleaseBuildError(
            "The frozen release manifest template has an unexpected shape."
        )
    expected_native_fields = {
        "packageId",
        "version",
        "relativePath",
        "sha256",
        "contractVersion",
        "contractDigest",
        "capabilityDigest",
        "pluginId",
    }
    native = manifest.get("native")
    if (
        not isinstance(native, dict)
        or set(native) != expected_native_fields
        or not isinstance(native.get("capabilityDigest"), str)
        or not SHA256_PATTERN.fullmatch(native["capabilityDigest"])
    ):
        raise ReleaseBuildError(
            "The frozen native release manifest has an unexpected shape."
        )
    return manifest


def _assemble_release(
    stage_root: Path,
    template: dict[str, Any],
    source_date_epoch: int,
    provider_build_id: str,
    tool_catalog: dict[str, Any],
    native_yak: bytes,
    capability_digest: str,
) -> bytes:
    if not SHA256_PATTERN.fullmatch(capability_digest):
        raise ReleaseBuildError("The compiled native capability digest is invalid.")
    catalog_bytes = canonical_json_bytes(tool_catalog)
    (stage_root / "contracts" / "tool-catalog.json").write_bytes(catalog_bytes)
    (stage_root / "native" / YAK_NAME).parent.mkdir(parents=True, exist_ok=True)
    (stage_root / "native" / YAK_NAME).write_bytes(native_yak)
    (stage_root / "SBOM.spdx.json").write_bytes(
        _sbom(stage_root, _runtime_lock(), native_yak, source_date_epoch)
    )

    files = release_tree_bytes(stage_root)
    expected = expected_release_members(
        {name for name in files if name.startswith("rhino_mcp/")},
        {name for name in files if name.startswith("contracts/")},
    )
    expected.update(name for name in files if name.startswith("runtime/python/"))
    expected.discard("manifest.json")
    expected.discard("file-manifest.json")
    if set(files) != expected:
        raise ReleaseBuildError(
            "The assembled outer release does not match the frozen file layout."
        )

    file_manifest_bytes = _write_file_manifest(files)
    (stage_root / "file-manifest.json").write_bytes(file_manifest_bytes)
    manifest = _safe_manifest(template)
    protocol_path = stage_root / "contracts" / "protocol.json"
    provider_build_path = stage_root / "contracts" / "provider-build.json"
    sbom_bytes = files["SBOM.spdx.json"]
    manifest["providerBuildId"] = sha256_digest(provider_build_path.read_bytes())
    if manifest["providerBuildId"] != provider_build_id:
        raise ReleaseBuildError(
            "Provider build ID differs from the staged canonical build record."
        )
    manifest["toolCatalogDigest"] = sha256_digest(catalog_bytes)
    manifest["filesDigest"] = sha256_digest(file_manifest_bytes)
    manifest["sbomDigest"] = sha256_digest(sbom_bytes)
    manifest["native"]["sha256"] = sha256_digest(native_yak)
    manifest["native"]["contractDigest"] = sha256_digest(protocol_path.read_bytes())
    manifest["native"]["capabilityDigest"] = capability_digest
    manifest_bytes = canonical_json_bytes(manifest)
    (stage_root / "manifest.json").write_bytes(manifest_bytes)

    all_files = release_tree_bytes(stage_root)
    return deterministic_zip_bytes(all_files, source_date_epoch)


def _write_archive(path: Path, payload: bytes) -> None:
    path = path.resolve()
    if path.exists():
        if path.is_file() and path.read_bytes() == payload:
            return
        raise ReleaseBuildError(
            f"Refusing to overwrite an existing release artifact: {path.name}."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def build_release(
    output_path: Path,
    *,
    runtime_archive: Path | None = None,
    dotnet_exe: str | None = None,
    uv_exe: str | None = None,
) -> tuple[Path, str]:
    lock = _runtime_lock()
    runtime_archive_path = _download_runtime_archive(lock, runtime_archive)
    dotnet = dotnet_exe or _dotnet_executable()
    uv = uv_exe or _uv_executable()
    source_date_epoch = lock.get("sourceDateEpoch")
    if not isinstance(source_date_epoch, int):
        raise ReleaseBuildError("The release SOURCE_DATE_EPOCH is missing or invalid.")

    temporary_root = _safe_ascii_temp_root()
    with tempfile.TemporaryDirectory(
        prefix="rhino-release-build-", dir=temporary_root
    ) as temporary:
        workspace = Path(temporary)
        stage_root = workspace / "release-root"
        stage_root.mkdir()
        (stage_root / "runtime" / "python").mkdir(parents=True)
        provider_files = _copy_provider_source(stage_root)
        _copy_contracts(stage_root)
        if (
            "runtime/python/Lib/site-packages/rhino_mcp/__init__.py"
            not in provider_files
        ):
            raise ReleaseBuildError("The flat Provider package entry point is missing.")
        if any(path.startswith(("provider/", "src/")) for path in provider_files):
            raise ReleaseBuildError(
                "The Provider package must remain at the flat rhino_mcp/ root."
            )

        _extract_python_runtime(
            runtime_archive_path, stage_root / "runtime" / "python", lock
        )
        _install_runtime_dependencies(stage_root, uv)
        build_record, provider_build_id = _build_identity(lock)
        provider_build_bytes = canonical_json_bytes(build_record)
        (stage_root / "contracts" / "provider-build.json").write_bytes(
            provider_build_bytes
        )
        _patch_build_identity(stage_root, provider_build_id)

        native_outputs = _build_native_outputs(workspace, dotnet, provider_build_id)
        native_records, yak_entries = _native_output_records(native_outputs)
        if not native_records or set(native_outputs) != {"net48", "net8.0"}:
            raise ReleaseBuildError(
                "Both Rhino 8 native target frameworks are required."
            )
        capability_probe = _build_native_capability_probe(
            workspace,
            dotnet,
            provider_build_id,
        )
        capability_digest = _native_capability_digest(
            native_outputs, dotnet, capability_probe
        )
        if f"guid:{PLUGIN_GUID}" not in yak_entries["manifest.yml"].decode("utf-8"):
            raise ReleaseBuildError(
                "The Yak manifest is missing the permanent Rhino plug-in GUID."
            )
        native_yak = build_yak_archive_bytes(yak_entries, source_date_epoch)

        try:
            catalog, counts = probe_managed_runtime(stage_root, provider_build_id)
        except ReleaseProbeError as exc:
            raise ReleaseBuildError(
                "The staged private Provider runtime failed discovery."
            ) from exc
        if counts != {"tools": 70, "resourcesAndTemplates": 4, "prompts": 2}:
            raise ReleaseBuildError(
                f"The staged MCP discovery surface changed unexpectedly: {counts}."
            )
        expected_catalog_path = (
            PROJECT_ROOT / "tests" / "fixtures" / "expected_mcp_catalog.json"
        )
        if expected_catalog_path.is_file():
            expected_catalog = json.loads(
                expected_catalog_path.read_text(encoding="utf-8")
            )
            if _frozen_tool_surface(catalog.get("tools")) != _frozen_tool_surface(
                expected_catalog.get("tools")
            ):
                raise ReleaseBuildError(
                    "The staged MCP tool catalog differs from the frozen contract fixture."
                )

        template = _read_json(RELEASE_TEMPLATE_PATH)
        archive = _assemble_release(
            stage_root,
            template,
            source_date_epoch,
            provider_build_id,
            catalog,
            native_yak,
            capability_digest,
        )
        archive_digest = sha256_digest(archive)
        _write_archive(output_path, archive)
        return output_path.resolve(), archive_digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dist" / ARCHIVE_NAME,
        help="destination ZIP; an existing different file is never overwritten",
    )
    parser.add_argument("--plugin-root", type=Path, default=PLUGIN_ROOT)
    parser.add_argument("--python-runtime-archive", type=Path)
    parser.add_argument("--dotnet-exe")
    parser.add_argument("--uv-exe")
    arguments = parser.parse_args(argv)
    try:
        configure_plugin_root(arguments.plugin_root)
        output, digest = build_release(
            arguments.output,
            runtime_archive=arguments.python_runtime_archive,
            dotnet_exe=arguments.dotnet_exe,
            uv_exe=arguments.uv_exe,
        )
    except ReleaseBuildError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Built {output.name} {digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit("artifactBuildFailed") from None
