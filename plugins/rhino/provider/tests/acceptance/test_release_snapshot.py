"""Opt-in real common-verifier race acceptance using a retained candidate."""

import hashlib
import os
import shutil
from pathlib import Path

import pytest
from scripts import release_cli, verify_release


@pytest.mark.skipif(
    not os.environ.get("RHINO_RACE_ARCHIVE"),
    reason="requires an actual common-verified candidate",
)
def test_original_paths_replaced_after_real_common_verification_do_not_change_native_bytes(
    monkeypatch, tmp_path
):
    root = Path(__file__).resolve().parents[3]
    original = Path(os.environ["RHINO_RACE_ARCHIVE"])
    original_receipt = Path(os.environ["RHINO_RACE_RECEIPT"])
    archive = tmp_path / "caller.zip"
    receipt = tmp_path / "caller.receipt.json"
    shutil.copyfile(original, archive)
    shutil.copyfile(original_receipt, receipt)
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected_receipt = receipt.read_bytes()
    output = tmp_path / "mapped.zip"
    real_run = release_cli.subprocess.run
    observed = []

    def verify_then_replace(command, **kwargs):
        result = real_run(command, **kwargs)
        assert result.returncode == 0
        observed.append("real-common-verified")
        archive.write_bytes(b"replaced after verification")
        receipt.write_bytes(b"replaced after verification")
        return result

    def inspect_native(yak_root, dotnet, capability, provider_build_id):
        assert (yak_root / "net48/RhinoMcpBridge.rhp").is_file()
        assert (yak_root / "net8.0/RhinoMcpBridge.rhp").is_file()
        observed.append("original-native-inspected")

    monkeypatch.setattr(release_cli.subprocess, "run", verify_then_replace)
    monkeypatch.setattr(verify_release, "_run_native_identity_test", inspect_native)
    release_cli.verify(
        root,
        archive,
        receipt,
        unsigned=True,
        trusted_keys=None,
        dotnet_exe="unused-test-dotnet",
        output=output,
    )
    assert observed == ["real-common-verified", "original-native-inspected"]
    assert hashlib.sha256(output.read_bytes()).hexdigest() == expected
    assert release_cli.receipt_path(output).read_bytes() == expected_receipt
    assert hashlib.sha256(original.read_bytes()).hexdigest() == expected
