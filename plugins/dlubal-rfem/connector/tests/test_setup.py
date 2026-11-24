import json
from pathlib import Path

import httpx
import pytest

from dlubal_mcp import sdk, setup
from dlubal_mcp.plugin_stdio import read_connection_settings


def test_prepare_selects_highest_installed_rfem_without_contacting_rfem(
    tmp_path, monkeypatch
):
    older = (Path("older.exe"), "6.14.0013.249")
    newer = (Path("newer.exe"), "6.16.0005.287")
    monkeypatch.setattr(setup, "installed_rfem", lambda: (newer, older))
    calls = []

    def prepare(directory, executable, build):
        calls.append((directory, executable, build))
        return sdk.select_sdk(build)

    monkeypatch.setattr(setup, "prepare_sdk", prepare)
    assert setup.prepare(tmp_path) == {
        "type": "completed",
        "build": newer[1],
        "sdkVersion": "2.16.5",
    }
    assert calls == [(tmp_path, *newer)]
    monkeypatch.setattr(setup, "installed_rfem", lambda: ())
    assert setup.prepare(tmp_path)["reason"] == "rfemNotInstalled"
    assert len(calls) == 1


def test_release_record_requires_exact_dlubal_build_and_official_wheel():
    name = "dlubal_api-2.16.5-py3-none-any.whl"
    record = {
        "filename": name,
        "url": "https://files.pythonhosted.org/packages/" + name,
        "size": 16,
        "yanked": False,
        "digests": {"sha256": "a" * 64},
    }
    release = {
        "info": {
            "version": "2.16.5",
            "description": "## Compatibility\n\n✅ **X.16.0005.287.74ddbf6781f**",
        },
        "urls": [record],
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=release))
    ) as client:
        assert sdk._release_record(client, "6.16.0005.287")["sha256"] == "a" * 64
        with pytest.raises(ValueError, match="rfemSdkBuildUnconfirmed"):
            sdk._release_record(client, "6.16.0005.299")
        record["url"] = record["url"].replace("files.pythonhosted.org", "example.com")
        with pytest.raises(ValueError, match="invalidRfemSdkDownload"):
            sdk._release_record(client, "6.16.0005.287")


def test_wheel_cache_reuses_only_valid_bytes(tmp_path):
    from hashlib import sha256

    payload = b"sdk fixture"
    record = {
        "filename": "sdk.whl",
        "url": "https://files.pythonhosted.org/sdk.whl",
        "size": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }
    cache = tmp_path / "cache"
    cache.mkdir()
    calls = []

    def fetch(request):
        calls.append(request.url)
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(fetch)) as client:
        path = sdk._download_wheel(client, record, cache, tmp_path)
        assert sdk._download_wheel(client, record, cache, tmp_path) == path
        assert len(calls) == 1
        path.write_bytes(b"bad content")
        assert (
            sdk._download_wheel(client, record, cache, tmp_path).read_bytes() == payload
        )
        assert len(calls) == 2
        path.write_bytes(b"bad content")
    invalid = httpx.MockTransport(lambda _: httpx.Response(200, content=b"wrong bytes"))
    with (
        httpx.Client(transport=invalid) as client,
        pytest.raises(ValueError, match="rfemSdkIntegrityFailed"),
    ):
        sdk._download_wheel(client, record, cache, tmp_path)
    assert path.read_bytes() == b"bad content"


def test_diagnostic_port_does_not_change_sdk_choice(monkeypatch):
    assert read_connection_settings().port == 9000
    monkeypatch.setenv(
        "PLUGIN_CONFIGURATION", json.dumps({"address": "localhost", "port": 9100})
    )
    settings = read_connection_settings()
    assert (settings.address, settings.port) == ("localhost", 9100)
    assert sdk.select_sdk("6.16.0005.287") == "2.16.5"
    monkeypatch.setenv("PLUGIN_CONFIGURATION", '{"address":"192.168.0.2"}')
    with pytest.raises(ValueError):
        read_connection_settings()


def test_service_version_checks_only_versions_returned_by_rfem():
    assert sdk.service_version_matches("6.16.0005.287", "6.16.0005")
    assert sdk.service_version_matches("6.16.0005.287", "6.16.0005.287")
    assert not sdk.service_version_matches("6.16.0005.287", "6.14.0013")
    assert not sdk.service_version_matches("6.16.0005.287", "6.16.0005.299")


def test_prepare_blocks_an_unresolved_legacy_execution(tmp_path, monkeypatch):
    from uuid import uuid4

    from dlubal_mcp.rfem.execution import ExecutionManager, ExecutionUncertain

    manager = ExecutionManager(tmp_path / "executions.sqlite3")

    def uncertain(*_):
        raise ExecutionUncertain("test outcome unavailable")

    identifier = str(uuid4())
    assert manager.submit(identifier, "thread", "test", uncertain)["state"] == "unknown"
    monkeypatch.setattr(
        setup, "installed_rfem", lambda: ((Path("rfem.exe"), "6.16.0005.287"),)
    )
    monkeypatch.setattr(
        setup, "prepare_sdk", lambda *_: pytest.fail("pending execution was ignored")
    )
    assert setup.prepare(tmp_path)["reason"] == "rfemExecutionUnresolved"
    assert manager.status(identifier)["state"] == "unknown"
