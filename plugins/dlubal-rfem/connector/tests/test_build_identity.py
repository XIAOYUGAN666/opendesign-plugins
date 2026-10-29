import pytest

from dlubal_rfem_mcp.core.identity import connector_build_id, verify_connector_build


def test_build_identity_rejects_wrong_host_attestation():
    with pytest.raises(ValueError, match="connectorBuildMismatch"):
        verify_connector_build("sha256:" + "0" * 64)
    verify_connector_build(connector_build_id())


def test_build_identity_tracks_source_and_lock_without_machine_path(tmp_path):
    source = tmp_path / "dlubal_rfem_mcp"
    source.mkdir()
    (source / "__init__.py").write_text('version = "0.1.0"', encoding="utf-8")
    (tmp_path / "uv.lock").write_text("frozen-wheel-hashes", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("build-inputs", encoding="utf-8")
    before = connector_build_id(source)
    (source / "__pycache__").mkdir()
    (source / "__pycache__/ignored.pyc").write_bytes(b"cache")
    assert connector_build_id(source) == before
    (source / "__init__.py").write_text('version = "0.2.0"', encoding="utf-8")
    assert connector_build_id(source) != before
    other = tmp_path / "second"
    import shutil

    shutil.copytree(source, other / "dlubal_rfem_mcp")
    for name in ("uv.lock", "pyproject.toml"):
        (other / name).write_bytes((tmp_path / name).read_bytes())
    assert connector_build_id(other / "dlubal_rfem_mcp") == connector_build_id(source)
