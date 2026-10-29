"""Approved official SDK value-only behavior, using only a test-owned INI.

The installed SDK is unmodified. No network or user's credential file is used.
"""

from unittest.mock import Mock

import pytest
from dlubal.api.common import connection
from dlubal.api.rfem import application


@pytest.fixture
def sdk_config(tmp_path, monkeypatch):
    path = tmp_path / "config.ini"
    content = b"[api_keys]\ndefault = fixture-default\nfixture = fixture-manual\n"
    path.write_bytes(content)
    monkeypatch.setattr(connection, "_get_config_path", lambda: path)
    return path, content


def test_official_value_only_reads_name_without_writing_or_replacing_value(sdk_config):
    path, original = sdk_config
    key = connection._ApiKey(value="fixture-manual")
    assert key.value == "fixture-manual"
    assert key.key == "fixture"
    assert path.read_bytes() == original


def test_official_value_only_does_not_import_default_key(sdk_config):
    path, original = sdk_config
    key = connection._ApiKey(value="fixture-new-manual")
    assert key.value == "fixture-new-manual"
    assert key.key is None
    assert path.read_bytes() == original


def test_application_passes_manual_value_to_channel_without_ini_write(
    sdk_config, monkeypatch
):
    class ConnectionPrevented(Exception):
        pass

    path, original = sdk_config
    channel = Mock(side_effect=ConnectionPrevented)
    monkeypatch.setattr(application, "init_channel", channel)
    with pytest.raises(ConnectionPrevented):
        application.Application(api_key_value="fixture-new-manual")
    assert channel.call_args.args[0] == "fixture-new-manual"
    assert path.read_bytes() == original
