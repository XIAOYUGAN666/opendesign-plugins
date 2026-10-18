"""The release archive and nested Yak must use the frozen multi-target layout."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import build_release
import pytest
import verify_release
from mcp.types import Tool, ToolAnnotations
from build_release import (
    PROJECT_ROOT,
    build_yak_archive_bytes,
    native_artifact_path,
    native_path_map,
    native_package_files,
    release_tree_bytes,
)
from release_probe import _normalize_tool
from verify_release import verify_yak_archive


def test_yak_uses_root_manifest_and_both_official_framework_directories() -> None:
    entries = {
        "manifest.yml": (
            b"name: rhino-mcp-bridge\n"
            b"version: 0.1.3\n"
            b"authors:\n  - Rhino MCP Developer\n"
            b"keywords:\n  - guid:74a1e8f4-1134-4686-a3e5-ee8bf393dec9\n"
            b"description: Managed Rhino Bridge.\n"
        ),
        "net48/RhinoMcpBridge.rhp": b"net48-plugin-fixture",
        "net48/Newtonsoft.Json.dll": b"net48-dependency-fixture",
        "net8.0/RhinoMcpBridge.rhp": b"net8-plugin-fixture",
        "net8.0/Newtonsoft.Json.dll": b"net8-dependency-fixture",
    }

    payload = build_yak_archive_bytes(entries, source_date_epoch=1_750_000_000)
    members = set(ZipFile(BytesIO(payload)).namelist())

    assert members == set(entries)
    assert "manifest.yml" in members
    assert "net48/RhinoMcpBridge.rhp" in members
    assert "net8.0/RhinoMcpBridge.rhp" in members
    verify_yak_archive(
        payload,
        expected_plugin_version="0.1.3",
        expected_plugin_guid="74a1e8f4-1134-4686-a3e5-ee8bf393dec9",
    )


def test_release_template_freezes_private_runtime_and_composite_files() -> None:
    from build_release import expected_release_members

    native = build_release.validate_root_identity()["extensions"]["io.github.xiaoyugan666.native"]["native"]

    members = expected_release_members(
        provider_files={"rhino_mcp/__init__.py"},
        contract_files={"contracts/protocol.json", "contracts/tool-catalog.json"},
    )

    assert {
        "manifest.json",
        "file-manifest.json",
        "SBOM.spdx.json",
        "runtime/python/python.exe",
        native["relativePath"],
        "rhino_mcp/__init__.py",
        "contracts/protocol.json",
        "contracts/tool-catalog.json",
    } <= members


def test_native_outputs_follow_the_official_dotnet_artifacts_pivot_layout(
    tmp_path,
) -> None:
    assert native_artifact_path(tmp_path, "net48") == (
        tmp_path / "bin" / "RhinoMcpBridge" / "release_net48"
    )


def test_release_manifest_freezes_the_signed_native_capability_digest() -> None:
    template = build_release._read_json(build_release.RELEASE_TEMPLATE_PATH)
    native = build_release.validate_root_identity()["extensions"]["io.github.xiaoyugan666.native"]["native"]
    assert template["native"]["capabilityDigest"] == native["capabilityDigest"]
    expected = "sha256:" + "a" * 64
    template["native"]["capabilityDigest"] = expected
    manifest = build_release._safe_manifest(template)

    assert manifest["native"]["capabilityDigest"] == expected


def test_release_template_includes_the_inventoried_rhino_8_build() -> None:
    manifest = build_release._safe_manifest(
        build_release._read_json(build_release.RELEASE_TEMPLATE_PATH)
    )

    assert "8.26.25349.19001" in manifest["software"]["supportedBuilds"]


def test_independent_builder_does_not_depend_on_host_workflow() -> None:
    source = (PROJECT_ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert "REPO_ROOT" not in source
    assert "build-rhino-plugin.yml" not in source


def test_native_capability_digest_is_read_from_both_compiled_targets(
    monkeypatch, tmp_path
) -> None:
    digest = "sha256:" + "a" * 64
    outputs = {}
    for target in ("net48", "net8.0"):
        output = tmp_path / target
        output.mkdir()
        plugin = output / "RhinoMcpBridge.rhp"
        plugin.write_bytes(b"compiled-native-plugin")
        outputs[target] = output
    probe = tmp_path / "BridgeTestHost.dll"
    probe.write_bytes(b"native-capability-probe")
    commands: list[list[str]] = []

    def run(command, *, cwd):
        commands.append(command)
        return digest

    monkeypatch.setattr(build_release, "_run", run)

    actual = build_release._native_capability_digest(outputs, "dotnet", probe)

    assert actual == digest
    assert [command[-1] for command in commands] == [
        str((outputs[target] / "RhinoMcpBridge.rhp").resolve())
        for target in ("net48", "net8.0")
    ]


def test_native_capability_digest_rejects_framework_drift(
    monkeypatch, tmp_path
) -> None:
    outputs = {}
    for target in ("net48", "net8.0"):
        output = tmp_path / target
        output.mkdir()
        (output / "RhinoMcpBridge.rhp").write_bytes(b"compiled-native-plugin")
        outputs[target] = output
    probe = tmp_path / "BridgeTestHost.dll"
    probe.write_bytes(b"native-capability-probe")
    digests = iter(("sha256:" + "a" * 64, "sha256:" + "b" * 64))
    monkeypatch.setattr(build_release, "_run", lambda command, *, cwd: next(digests))

    with pytest.raises(
        build_release.ReleaseBuildError, match="target frameworks disagree"
    ):
        build_release._native_capability_digest(outputs, "dotnet", probe)


def test_release_verifier_checks_signed_capability_digest_against_both_targets(
    monkeypatch, tmp_path
) -> None:
    expected = "sha256:" + "a" * 64
    observed: dict[str, object] = {}

    def run(command, *, cwd, env, **kwargs):
        observed.update(command=command, env=env)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(verify_release.subprocess, "run", run)

    verify_release._run_native_identity_test(tmp_path, "dotnet", expected)

    assert observed["env"]["RHINO_EXPECTED_CAPABILITY_DIGEST"] == expected


@pytest.mark.parametrize("longer_source", ["checkout", "build"])
def test_native_release_path_map_stabilizes_repository_and_ephemeral_build_paths(
    tmp_path, monkeypatch, longer_source,
) -> None:
    checkout = tmp_path / ("long-checkout-source" if longer_source == "checkout" else "src")
    workspace = tmp_path / ("long-temporary-build" if longer_source == "build" else "out")
    checkout.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(build_release, "PLUGIN_ROOT", checkout)
    mappings = [entry.rsplit("=", 1) for entry in native_path_map(workspace).split("%2C")]
    assert dict(mappings) == {
        str(checkout.resolve()): "/_/rhino",
        str(workspace.resolve()): "/_/rhino-build",
    }
    assert len(mappings[0][0]) >= len(mappings[1][0])
    assert native_artifact_path(tmp_path, "net8.0") == (
        tmp_path / "bin" / "RhinoMcpBridge" / "release_net8.0"
    )
    assert build_release.native_capability_probe_path(tmp_path) == (
        tmp_path / "bin" / "BridgeTestHost" / "release" / "BridgeTestHost.dll"
    )


def test_yak_inputs_keep_native_assemblies_and_exclude_debug_symbols(tmp_path) -> None:
    output = tmp_path / "release_net48"
    output.mkdir()
    (output / "RhinoMcpBridge.rhp").write_bytes(b"native-plugin")
    (output / "Newtonsoft.Json.dll").write_bytes(b"runtime-dependency")
    (output / "RhinoMcpBridge.pdb").write_bytes(b"debug-symbols")

    assert native_package_files(output, "net48") == {
        "net48/RhinoMcpBridge.rhp": b"native-plugin",
        "net48/Newtonsoft.Json.dll": b"runtime-dependency",
    }


def test_provider_build_identity_is_computable_before_native_outputs(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        build_release,
        "_source_records",
        lambda: [{"path": "rhino_mcp/a.py", "sha256": "source"}],
    )
    monkeypatch.setattr(
        build_release,
        "_contract_records",
        lambda: [{"path": "contracts/a.json", "sha256": "contract"}],
    )
    monkeypatch.setattr(
        build_release,
        "_build_inputs",
        lambda: [{"path": "pyproject.toml", "sha256": "input"}],
    )
    monkeypatch.setattr(
        build_release,
        "_native_input_records",
        lambda: [{"path": "native/Bridge.cs", "sha256": "native-source"}],
    )

    record, provider_build_id = build_release._build_identity(
        {
            "python": {
                "version": "3.12.10",
                "architecture": "x86_64",
                "byteLength": 42,
                "sha256": "sha256:" + "a" * 64,
            }
        }
    )

    assert set(record["native"]) == {"targetFrameworks", "pluginId", "inputs"}
    assert provider_build_id.startswith("sha256:")


def test_native_build_embeds_the_precomputed_provider_identity(
    monkeypatch, tmp_path
) -> None:
    provider_build_id = "sha256:" + "b" * 64
    commands: list[list[str]] = []

    def artifact_path(artifacts, target):
        output = tmp_path / f"release_{target}"
        output.mkdir(exist_ok=True)
        return output

    monkeypatch.setattr(
        build_release, "_run", lambda command, cwd: commands.append(command)
    )
    monkeypatch.setattr(build_release, "native_artifact_path", artifact_path)
    monkeypatch.setattr(
        build_release,
        "native_package_files",
        lambda directory, target: {f"{target}/RhinoMcpBridge.rhp": b"plugin"},
    )

    outputs = build_release._build_native_outputs(tmp_path, "dotnet", provider_build_id)

    build_command = next(command for command in commands if command[1] == "build")
    assert set(outputs) == {"net48", "net8.0"}
    assert f"-p:RhinoProviderBuildId={provider_build_id}" in build_command


def test_release_probe_digest_normalization_matches_the_official_mcp_tool_model() -> (
    None
):
    tool = Tool(
        name="inspect",
        title="Inspect",
        description="Read the current document.",
        inputSchema={"type": "object"},
        outputSchema={"type": "object"},
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        _meta={"fastmcp": {"tags": ["rhino"]}},
    )
    wire_value = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    digest_fields = (
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "_meta",
    )
    expected = {key: wire_value[key] for key in digest_fields if key in wire_value}

    assert _normalize_tool(tool) == expected


def test_frozen_tool_census_checks_core_contract_without_dropping_digest_fields() -> (
    None
):
    expected = {
        "name": "inspect",
        "description": "Read the current document.",
        "inputSchema": {"type": "object"},
        "annotations": {"title": None, "readOnlyHint": True},
    }
    actual = {
        **expected,
        "title": "Inspect",
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
        "_meta": {"fastmcp": {"tags": ["rhino"]}},
    }

    assert build_release._frozen_tool_surface([actual]) == [expected]


def test_release_tree_keeps_only_the_private_python_entrypoint_executable(
    tmp_path,
) -> None:
    runtime = tmp_path / "runtime" / "python"
    private_entrypoint = runtime / "python.exe"
    dependency_launcher = runtime / "Lib" / "site-packages" / "bin" / "mcp.exe"
    package_example = runtime / "Lib" / "site-packages" / "pythonwin" / "Pythonwin.exe"
    extension_module = runtime / "Lib" / "site-packages" / "win32" / "pythoncom312.dll"
    for path, payload in (
        (private_entrypoint, b"python-runtime"),
        (dependency_launcher, b"console-launcher"),
        (package_example, b"optional-gui"),
        (extension_module, b"required-extension"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    files = release_tree_bytes(tmp_path)

    assert "runtime/python/python.exe" in files
    assert "runtime/python/Lib/site-packages/bin/mcp.exe" not in files
    assert "runtime/python/Lib/site-packages/pythonwin/Pythonwin.exe" not in files
    assert "runtime/python/Lib/site-packages/win32/pythoncom312.dll" in files


def test_release_tree_excludes_dependency_tests_but_keeps_runtime_license_sources(
    tmp_path,
) -> None:
    dependency_tests = (
        tmp_path
        / "runtime"
        / "python"
        / "Lib"
        / "site-packages"
        / "jsonschema"
        / "tests"
        / "test_validators.py"
    )
    license_source = (
        tmp_path
        / "runtime"
        / "python"
        / "Lib"
        / "site-packages"
        / "pywin32-312.dist-info"
        / "licenses"
        / "src"
        / "LICENSE"
    )
    dependency_tests.parent.mkdir(parents=True, exist_ok=True)
    license_source.parent.mkdir(parents=True, exist_ok=True)
    dependency_tests.write_text("test-only", encoding="utf-8")
    license_source.write_text("license attribution", encoding="utf-8")

    files = release_tree_bytes(tmp_path)

    assert (
        "runtime/python/Lib/site-packages/jsonschema/tests/test_validators.py"
        not in files
    )
    assert (
        "runtime/python/Lib/site-packages/pywin32-312.dist-info/licenses/src/LICENSE"
        in files
    )
