"""The embedded interpreter must not inherit developer or user Python paths."""

from __future__ import annotations

from build_release import embedded_pth_content, managed_bootstrap_command


def test_embedded_pth_is_explicit_and_restricts_search_roots() -> None:
    assert embedded_pth_content() == (
        "python312.zip\n.\nLib\\site-packages\nimport site\n"
    )


def test_managed_runtime_command_uses_isolated_private_interpreter() -> None:
    assert managed_bootstrap_command("runtime/python/python.exe") == (
        "runtime/python/python.exe",
        "-I",
        "-s",
        "-B",
        "-m",
        "rhino_mcp.managed_bootstrap",
    )
