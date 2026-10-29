"""Keep the Rhino-native package boundary and Yak manifest clean."""

from __future__ import annotations

import re
import json
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
NATIVE_ROOT = PLUGIN_ROOT / "native"

FORBIDDEN_PRODUCTION_TEXT = (
    "net7.0",
    "Commands/",
    "MCP" + "StartCommand",
    "MCP" + "StopCommand",
    "MCP" + "TestCommand",
    "MCP" + "VersionCommand",
    "TestAllFunctions",
    "TestGrasshopperFunctions",
    "install.sh",
    "launchSettings.json",
    "RHINO_MCP_" + "HOST",
    "RHINO_MCP_" + "ALLOW_REMOTE",
)


def test_native_package_contains_no_legacy_cli() -> None:
    assert NATIVE_ROOT.is_dir()
    production_files = [
        path
        for path in NATIVE_ROOT.rglob("*")
        if path.is_file() and "tests" not in path.relative_to(NATIVE_ROOT).parts
    ]
    assert production_files

    for path in production_files:
        if path.suffix.lower() not in {".cs", ".csproj", ".sln", ".yml", ".yaml"}:
            continue
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PRODUCTION_TEXT:
            assert forbidden not in source, f"{forbidden!r} leaked into {path}"

    assert (PLUGIN_ROOT / "connector" / "pyproject.toml").is_file()
    assert not (PLUGIN_ROOT / "src").exists()


def test_rhino_8_bridge_uses_the_official_windows_target_matrix() -> None:
    project = (NATIVE_ROOT / "RhinoMcpBridge.csproj").read_text(encoding="utf-8")

    assert re.search(
        r"(?m)^\s*<TargetFrameworks>net48;net8\.0</TargetFrameworks>\s*$",
        project,
    )


def test_net48_build_pins_a_language_version_with_nullable_support() -> None:
    project = (NATIVE_ROOT / "RhinoMcpBridge.csproj").read_text(encoding="utf-8")

    assert re.search(r"(?m)^\s*<LangVersion>12\.0</LangVersion>\s*$", project)


def test_native_builds_suppress_the_documented_rhinocommon_fallback_warning() -> None:
    project_paths = (
        NATIVE_ROOT / "RhinoMcpBridge.csproj",
        NATIVE_ROOT / "tests" / "RhinoMcpBridge.Tests.csproj",
    )

    for project_path in project_paths:
        project = project_path.read_text(encoding="utf-8")
        assert re.search(r"(?m)^\s*<NoWarn>[^<]*NU1701", project)


def test_yak_manifest_has_required_identity_without_license_or_public_urls() -> None:
    manifest_path = NATIVE_ROOT / "package" / "manifest.yml"
    manifest = manifest_path.read_text(encoding="utf-8")

    assert re.search(r"(?m)^name:\s*rhino-mcp-bridge\s*$", manifest)
    version = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))[
        "version"
    ]
    assert re.search(r"(?m)^version:\s*" + re.escape(version) + r"\s*$", manifest)
    assert re.search(r"(?m)^authors:\s*$", manifest)
    assert re.search(r"(?m)^\s+-\s+Rhino MCP Developer\s*$", manifest)
    assert re.search(r"(?m)^description:\s*\S", manifest)
    assert "license:" not in manifest.lower()
    assert "copyright:" not in manifest.lower()
    assert not re.search(
        r"(?m)^(url|icon|icon_url|source|repository|update_url):", manifest
    )


def test_upstream_only_directory_layers_are_not_reintroduced() -> None:
    assert not any(
        path.name.lower() in {"src"} for path in PLUGIN_ROOT.iterdir() if path.is_dir()
    )
