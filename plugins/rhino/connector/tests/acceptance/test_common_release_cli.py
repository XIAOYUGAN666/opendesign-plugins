"""The PS adapter preserves caller options and one verified archive/receipt pair."""

import json
from pathlib import Path

import pytest
from scripts import build_plugin, build_release, release_cli


def test_release_adapter_carries_publisher_sequence_to_common_validation_and_finalizer(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    observed = {}

    def check_inputs(root, *, release_sequence):
        observed["checked"] = release_sequence

    def finish(*args, release_sequence):
        observed["finalized"] = release_sequence
        return tmp_path / "candidate.zip"

    common = SimpleNamespace(
        check_source_closure=lambda root: None,
        check_release_inputs=check_inputs,
        _finish_staged_artifact=finish,
    )
    monkeypatch.setattr(release_cli, "_common_builder", lambda root: common)
    monkeypatch.setattr(build_plugin, "stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(release_cli, "verify", lambda *args, **kwargs: None)
    release_cli.build(
        tmp_path,
        tmp_path / "output.zip",
        release_sequence=2,
        runtime_archive=None,
        dotnet_exe=None,
        uv_exe=None,
    )
    assert observed == {"checked": 2, "finalized": 2}


def test_pair_mapping_keeps_verified_bytes_and_refuses_existing_receipt(tmp_path):
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"reviewed archive")
    receipt = tmp_path / "unsigned-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "archive": {
                    "byteLength": archive.stat().st_size,
                    "sha256": build_release.sha256_digest(archive.read_bytes()),
                }
            }
        )
    )
    output = tmp_path / "mapped/custom.zip"
    release_cli.copy_verified_pair(archive, receipt, output)
    assert output.read_bytes() == archive.read_bytes()
    assert release_cli.receipt_path(output).read_bytes() == receipt.read_bytes()
    output.unlink()
    with pytest.raises(ValueError, match="outputMustBeFresh"):
        release_cli.copy_verified_pair(archive, receipt, output)
    assert not output.exists()


def test_pair_mapping_rejects_unmatched_archive_without_creating_outputs(tmp_path):
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(b"changed")
    receipt = tmp_path / "unsigned-receipt.json"
    receipt.write_text(
        json.dumps({"archive": {"byteLength": 1, "sha256": "sha256:" + "0" * 64}})
    )
    output = tmp_path / "output.zip"
    with pytest.raises(ValueError, match="archiveReceiptMismatch"):
        release_cli.copy_verified_pair(archive, receipt, output)
    assert not output.exists()
    assert not release_cli.receipt_path(output).exists()


def test_stage_passes_the_supplied_runtime_and_toolchain(monkeypatch, tmp_path):
    import zipfile

    root = Path(__file__).resolve().parents[3]
    runtime = tmp_path / "caller-python.zip"
    runtime.write_bytes(b"verified by runtime checker")
    monkeypatch.setattr(build_release, "_verify_runtime_archive", lambda *args: None)
    observed = {}

    def download(lock, supplied):
        assert supplied == runtime
        return runtime

    def build(output, **kwargs):
        observed.update(kwargs)
        with zipfile.ZipFile(output, "w"):
            pass

    monkeypatch.setattr(build_release, "_download_runtime_archive", download)
    monkeypatch.setattr(build_release, "build_release", build)
    monkeypatch.setattr(build_plugin, "convert_stage", lambda *args: None)
    build_plugin.stage(
        root,
        tmp_path / "stage",
        tmp_path / "cache",
        runtime_archive=runtime,
        dotnet_exe="chosen-dotnet",
        uv_exe="chosen-uv",
    )
    assert observed == {
        "runtime_archive": runtime,
        "dotnet_exe": "chosen-dotnet",
        "uv_exe": "chosen-uv",
    }


def test_verification_requires_caller_controlled_authority(tmp_path):
    root = Path(__file__).resolve().parents[3]
    with pytest.raises(ValueError, match="trustedKeysRequired"):
        release_cli.verify(
            root,
            tmp_path / "archive",
            tmp_path / "receipt",
            unsigned=False,
            trusted_keys=None,
            dotnet_exe=None,
        )


@pytest.mark.parametrize("directory", [False, True])
def test_explicit_invalid_runtime_never_downloads_or_creates_caller_output(
    monkeypatch, tmp_path, directory
):
    root = Path(__file__).resolve().parents[3]
    runtime = tmp_path / "explicit-runtime.zip"
    if directory:
        runtime.mkdir()
    downloaded = []

    def download(*args):
        downloaded.append(args)
        raise RuntimeError("download-dispatched")

    monkeypatch.setattr(build_release, "_download_runtime_archive", download)
    output = tmp_path / "caller-output"
    with pytest.raises(build_release.ReleaseBuildError):
        build_plugin.stage(root, output, tmp_path / "cache", runtime_archive=runtime)
    assert downloaded == []
    assert not output.exists()


def test_snapshot_read_is_bounded(tmp_path):
    path = tmp_path / "oversized.bin"
    path.write_bytes(b"12345")
    with pytest.raises(ValueError, match="invalidArtifactSize"):
        release_cli._bounded_read(path, 4)
