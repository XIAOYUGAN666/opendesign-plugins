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
        "connector/pyproject.toml",
        "connector/uv.lock",
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
        if p["name"] in {"rhino-mcp-connector", "rhino-mcp-bridge"}
    ]
    assert len(owned) == 2
    assert {p["versionInfo"] for p in owned} == {"9.8.7"}
    assert sbom["name"] == "rhino-connector-release-9.8.7"
    assert sbom["documentNamespace"].startswith(
        "https://spdx.org/spdxdocs/rhino-connector-release-9.8.7-"
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
        'CONNECTOR_BUILD_ID = "<connector-build-id>"', staged.read_bytes().decode("utf-8")
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


@pytest.mark.parametrize("explicit_uv", [False, True])
def test_release_installs_own_distribution_before_native_build(
    tmp_path, monkeypatch, explicit_uv
):
    """Exercise real Python staging without downloading or building Rhino."""
    from importlib.metadata import Distribution

    monkeypatch.setattr(build_release, "_download_runtime_archive", lambda *a: tmp_path)
    monkeypatch.setattr(build_release, "_extract_python_runtime", lambda *a: None)
    monkeypatch.setattr(build_release, "_install_runtime_dependencies", lambda *a: None)
    selected_uv = build_release._uv_executable()
    if explicit_uv:
        monkeypatch.setenv("PATH", "")
    uv_lookups = []

    def lookup_uv():
        uv_lookups.append(True)
        return selected_uv

    monkeypatch.setattr(build_release, "_uv_executable", lookup_uv)

    class CheckedStage(Exception):
        pass

    def check_stage(workspace, dotnet, identity):
        if explicit_uv:
            assert not uv_lookups, (
                "release ignored its explicitly selected uv executable"
            )
        base = workspace / "release-root/runtime/python/Lib/site-packages"
        matches = [
            d
            for d in Distribution.discover(path=[str(base)])
            if d.metadata["Name"] == "rhino-mcp-connector"
        ]
        assert len(matches) == 1, "release omitted its own distribution metadata"
        dist = matches[0]
        assert dist.version == build_release.validate_root_identity()["version"]
        assert dist.read_text("direct_url.json") is None
        assert not list(base.glob("*.pth"))
        # Wheel RECORD must describe the final patched source, not the checkout.
        record = next(f for f in dist.files if str(f) == "rhino_mcp/build_identity.py")
        import base64
        import hashlib

        actual = (
            base64.urlsafe_b64encode(
                hashlib.sha256(dist.locate_file(record).read_bytes()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        assert record.hash.value == actual
        assert identity in dist.locate_file(record).read_text("utf-8")
        raise CheckedStage

    monkeypatch.setattr(build_release, "_build_native_outputs", check_stage)
    with pytest.raises(CheckedStage):
        build_release.build_release(
            tmp_path / "unused.zip",
            dotnet_exe="unused",
            uv_exe=selected_uv if explicit_uv else None,
        )


def test_runtime_inventory_separates_own_distribution_from_registry_wheels(tmp_path):
    from scripts.build_plugin import release, runtime_inventory

    version = build_release.validate_root_identity()["version"]
    metadata = (
        tmp_path
        / f"runtime/python/Lib/site-packages/rhino_mcp_connector-{version}.dist-info/METADATA"
    )
    metadata.parent.mkdir(parents=True)
    metadata.write_text(f"Name: rhino-mcp-connector\nVersion: {version}\n")
    inventory = runtime_inventory(tmp_path, build_release.PLUGIN_ROOT, {})
    assert inventory["wheels"] == []
    metadata.write_text("Name: rhino-mcp-connector\nVersion: 9.9\n")
    with pytest.raises(release.ReleaseBuildError):
        runtime_inventory(tmp_path, build_release.PLUGIN_ROOT, {})


def test_sbom_lists_the_installed_provider_only_once(tmp_path):
    import json

    version = build_release.validate_root_identity()["version"]
    path = (
        tmp_path
        / f"runtime/python/Lib/site-packages/rhino_mcp_connector-{version}.dist-info/METADATA"
    )
    path.parent.mkdir(parents=True)
    path.write_text(f"Name: rhino-mcp-connector\nVersion: {version}\n")
    sbom = json.loads(
        build_release._sbom(tmp_path, build_release._runtime_lock(), b"fixture", 0)
    )
    assert len([p for p in sbom["packages"] if p["name"] == "rhino-mcp-connector"]) == 1


def test_release_refuses_unlocked_build_backend(tmp_path, monkeypatch):
    import shutil
    import re

    stage = tmp_path / "stage"
    build_release._copy_provider_source(stage)
    copied = tmp_path / "unlocked-project"
    copied.mkdir()
    original = (build_release.PROJECT_ROOT / "pyproject.toml").read_text("utf-8")
    (copied / "pyproject.toml").write_text(
        re.sub(r"(?m)^build = \[.*\]$", 'build = ["setuptools==0.0.0"]', original),
        encoding="utf-8",
    )
    shutil.copyfile(build_release.PROJECT_ROOT / "uv.lock", copied / "uv.lock")
    monkeypatch.setattr(build_release, "PROJECT_ROOT", copied)
    with pytest.raises(build_release.ReleaseBuildError):
        build_release._install_provider_distribution(
            stage, build_release._uv_executable()
        )


def test_provider_wheel_metadata_is_identical_across_build_directories(tmp_path):
    snapshots = []
    for directory in ("first", "second"):
        stage = tmp_path / directory / "stage"
        build_release._copy_provider_source(stage)
        build_release._patch_build_identity(stage, "sha256:" + "b" * 64)
        build_release._install_provider_distribution(
            stage, build_release._uv_executable()
        )
        base = stage / "runtime/python/Lib/site-packages"
        snapshots.append(
            {
                p.relative_to(base).as_posix(): p.read_bytes()
                for p in base.glob("*.dist-info/*")
                if p.is_file()
            }
        )
    assert snapshots[0]
    assert snapshots[0] == snapshots[1]


def test_formal_powershell_entrypoints_use_the_common_artifact_chain():
    root = Path(__file__).resolve().parents[2]
    build = (root / "scripts/build-release.ps1").read_text(encoding="utf-8")
    verify = (root / "scripts/verify-release.ps1").read_text(encoding="utf-8")
    assert "scripts.release_cli" in build
    assert "scripts.release_cli" in verify
    assert "scripts/build_release.py" not in build
    assert "scripts/verify_release.py" not in verify
