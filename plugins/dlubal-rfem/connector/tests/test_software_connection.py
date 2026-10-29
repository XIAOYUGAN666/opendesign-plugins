import asyncio
import io
import json
import threading
from contextlib import contextmanager
from typing import cast
from unittest.mock import patch

import pytest

from dlubal_rfem_mcp.rfem.session import RFEMBusy, RFEMSession
from dlubal_rfem_mcp.rfem.software_connection import RFEMSoftwareConnection


class FixtureSession:
    def __init__(self):
        self.methods = []
        self.restriction = None
        self.started = threading.Event()
        self.finish = threading.Event()
        self.finish.set()

    @contextmanager
    def connection(self, timeout=0):
        self.methods.append("open")
        try:
            # Mirrors RFEMSession.connection(): a newly opened session validates
            # the application build through this Service call exactly once.
            self.get_application_info()
            yield self
        finally:
            self.methods.append("close_connection")

    def get_application_info(self):
        self.methods.append("get_application_info")
        self.started.set()
        self.finish.wait(2)


@pytest.mark.asyncio
async def test_rfem_connection_uses_only_service_and_reports_blocked_prerequisite():
    session = FixtureSession()
    adapter = RFEMSoftwareConnection(
        cast(RFEMSession, session), software_running=lambda: "si_fixture"
    )
    assert (await adapter.ensure_connection(1)).outcome == "confirmed"
    assert session.methods == ["open", "get_application_info", "close_connection"]
    missing = RFEMSoftwareConnection(
        cast(RFEMSession, session), software_running=lambda: None
    )
    assert (await missing.ensure_connection(1)).reason == "softwareNotRunning"
    assert len(session.methods) == 3


@pytest.mark.asyncio
async def test_timed_out_check_is_not_duplicated_while_worker_remains_active():
    session = FixtureSession()
    session.finish.clear()
    adapter = RFEMSoftwareConnection(
        cast(RFEMSession, session), software_running=lambda: "si_fixture"
    )
    try:
        result = await adapter.ensure_connection(0.02)
        assert result.outcome == "unconfirmed"
        assert session.started.is_set()
        result = await adapter.ensure_connection(0.02)
        assert result.outcome == "deferred"
        assert session.methods.count("get_application_info") == 1
    finally:
        session.finish.set()
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_missing_credentials_and_busy_session_do_not_trigger_service_calls():
    missing = RFEMSoftwareConnection(None, software_running=lambda: "si_fixture")
    assert (await missing.ensure_connection(1)).reason == "credentialMissing"

    class BusySession(FixtureSession):
        @contextmanager
        def connection(self, timeout=0):
            raise RFEMBusy("busy")
            yield self

    session = BusySession()
    adapter = RFEMSoftwareConnection(
        cast(RFEMSession, session), software_running=lambda: "si_fixture"
    )
    assert (await adapter.ensure_connection(1)).outcome == "deferred"
    assert session.methods == []


@pytest.mark.asyncio
async def test_software_replacement_during_check_cannot_reuse_old_success():
    session = FixtureSession()
    session.finish.clear()
    instance = ["si_old"]
    adapter = RFEMSoftwareConnection(
        cast(RFEMSession, session), software_running=lambda: instance[0]
    )
    task = asyncio.create_task(adapter.ensure_connection(1))
    try:
        while not session.started.is_set():
            await asyncio.sleep(0.001)
        instance[0] = "si_new"
        session.finish.set()
        assert (await task).outcome == "unconfirmed"
    finally:
        session.finish.set()


def test_engineering_tools_and_connection_check_share_one_session(envelope):
    from dlubal_rfem_mcp.core.server import build_mcp
    from dlubal_rfem_mcp.managed_bootstrap import read_connector_envelope

    parsed = read_connector_envelope(io.BytesIO(json.dumps(envelope).encode()))
    session = FixtureSession()
    with patch("dlubal_rfem_mcp.rfem.tools.register_tools", return_value=[]) as register:
        build_mcp(parsed, session=session)
    assert register.call_args.args[1] is session
