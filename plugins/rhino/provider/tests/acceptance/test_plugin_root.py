from pathlib import Path

import build_release
import pytest


def test_build_root_uses_plugin_owned_native_and_contract_inputs():
    root = Path(__file__).resolve().parents[3]
    assert build_release.NATIVE_ROOT == root / "native"
    records = build_release._contract_records()
    assert {record["path"] for record in records} >= {"contracts/protocol.json"}
    assert {record["path"] for record in build_release._build_inputs()} >= {
        "global.json",
        "provider/pyproject.toml",
        "provider/uv.lock",
    }


def test_support_is_limited_to_the_approved_rhino_build():
    template = build_release._read_json(build_release.RELEASE_TEMPLATE_PATH)
    assert template["software"]["supportedBuilds"] == ["8.26.25349.19001"]


def test_build_record_uses_validated_root_version(monkeypatch):
    identity = build_release.validate_root_identity()
    monkeypatch.setattr(
        build_release,
        "validate_root_identity",
        lambda: {**identity, "version": "9.8.7"},
    )
    record, _ = build_release._build_identity(build_release._runtime_lock())
    assert record["releaseVersion"] == "9.8.7"


def test_candidate_filenames_follow_the_selected_root_version():
    identity = build_release.validate_root_identity()
    build_release.configure_plugin_root(build_release.PLUGIN_ROOT)
    assert (
        build_release.YAK_NAME == f"rhino-mcp-bridge-{identity['version']}-rh8-win.yak"
    )
    assert (
        build_release.ARCHIVE_NAME == f"rhino-{identity['version']}-windows-x86_64.zip"
    )


def test_sbom_uses_the_validated_plugin_version(tmp_path, monkeypatch):
    import json

    identity = build_release.validate_root_identity()
    monkeypatch.setattr(
        build_release,
        "validate_root_identity",
        lambda: {**identity, "version": "9.8.7"},
    )
    sbom = json.loads(
        build_release._sbom(
            tmp_path, build_release._runtime_lock(), b"synthetic-yak", 0
        )
    )
    owned = [
        p
        for p in sbom["packages"]
        if p["name"] in {"rhino-mcp-provider", "rhino-mcp-bridge"}
    ]
    assert len(owned) == 2
    assert {p["versionInfo"] for p in owned} == {"9.8.7"}
    assert sbom["name"] == "rhino-provider-release-9.8.7"
    assert sbom["documentNamespace"].startswith(
        "https://spdx.org/spdxdocs/rhino-provider-release-9.8.7-"
    )


def test_release_identity_rejects_derived_version_drift(tmp_path):
    import json
    import shutil
    import pytest

    root = Path(__file__).resolve().parents[3]
    copied = tmp_path / "plugin"
    shutil.copytree(
        root,
        copied,
        ignore=shutil.ignore_patterns(".venv", "bin", "obj", "__pycache__"),
    )
    identity = json.loads((copied / "plugin.json").read_text())
    identity["version"] += "-drift"
    (copied / "plugin.json").write_text(json.dumps(identity))
    original = build_release.PLUGIN_ROOT
    try:
        build_release.configure_plugin_root(copied)
        with pytest.raises(build_release.ReleaseBuildError, match="identity"):
            build_release._build_identity(build_release._runtime_lock())
    finally:
        build_release.configure_plugin_root(original)


@pytest.mark.parametrize("assembly", ["RhinoCommon.dll", "Eto.Wpf.dll"])
def test_native_packaging_rejects_software_owned_assemblies(tmp_path, assembly):
    import pytest

    (tmp_path / "RhinoMcpBridge.rhp").write_bytes(b"plugin")
    (tmp_path / assembly).write_bytes(b"software")
    with pytest.raises(build_release.ReleaseBuildError, match="software"):
        build_release.native_package_files(tmp_path, "net8.0")


def test_private_release_stages_provider_in_the_public_runtime_layout(tmp_path):
    build_release._copy_provider_source(tmp_path)
    assert (
        tmp_path / "runtime/python/Lib/site-packages/rhino_mcp/managed_bootstrap.py"
    ).is_file()
    assert not (tmp_path / "rhino_mcp").exists()


def test_staged_build_identity_only_replaces_the_declared_identity(tmp_path):
    import hashlib

    build_release._copy_provider_source(tmp_path)
    identity = "sha256:" + "b" * 64
    build_release._patch_build_identity(tmp_path, identity)
    staged = tmp_path / "runtime/python/Lib/site-packages/rhino_mcp/build_identity.py"
    normalized, count = build_release.BUILD_ID_PATTERN.subn(
        'PROVIDER_BUILD_ID = "<provider-build-id>"', staged.read_bytes().decode("utf-8")
    )
    assert count == 1
    record = next(
        r
        for r in build_release._source_records()
        if r["path"] == "rhino_mcp/build_identity.py"
    )
    assert (
        record["sha256"]
        == "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    )


def test_formal_powershell_entrypoints_use_the_common_artifact_chain():
    root = Path(__file__).resolve().parents[2]
    build = (root / "scripts/build-release.ps1").read_text(encoding="utf-8")
    verify = (root / "scripts/verify-release.ps1").read_text(encoding="utf-8")
    assert "scripts.release_cli" in build
    assert "scripts.release_cli" in verify
    assert "scripts/build_release.py" not in build
    assert "scripts/verify_release.py" not in verify
