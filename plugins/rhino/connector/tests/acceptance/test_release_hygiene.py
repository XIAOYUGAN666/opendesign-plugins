"""Only audited, relative release members may enter the immutable archive."""

from __future__ import annotations

import pytest

from verify_release import ReleaseVerificationError, validate_release_hygiene


def test_release_hygiene_accepts_flat_package_and_native_targets() -> None:
    validate_release_hygiene(
        {
            "manifest.json",
            "file-manifest.json",
            "SBOM.spdx.json",
            "runtime/python/python.exe",
            "runtime/python/Lib/site-packages/pywin32-312.dist-info/licenses/src/LICENSE",
            "rhino_mcp/managed_bootstrap.py",
            "native/rhino-mcp-bridge-0.1.3-rh8-win.yak",
            "contracts/protocol.json",
        }
    )


@pytest.mark.parametrize(
    "member",
    [
        "../outside.dll",
        "connector/server.py",
        "src/server.py",
        "tests/BridgeTestHost.exe",
        "runtime/python/Lib/site-packages/jsonschema/tests/test_validators.py",
        "runtime/python/python._pth.pyd",
        "native/RhinoMcpBridge.pdb",
        "runtime/python/old-install.ps1",
        "mcp" + "start.cmd",
        "runtime/python/python311.dll",
        f"rhino_mcp/{'open' + 'design'}/payload.py",
    ],
)
def test_release_hygiene_rejects_unsafe_or_development_members(member: str) -> None:
    with pytest.raises(ReleaseVerificationError):
        validate_release_hygiene({member})
