import asyncio
import threading

import pytest

from rhino_mcp.bridge import BridgeBusy, BridgeClient
from rhino_mcp.software_connection import RhinoSoftwareConnection
from tests.fixtures.bridge_contract import FakeBridge, bridge_config


@pytest.mark.asyncio
async def test_missing_rhino_or_native_peer_are_explicit_hard_conditions():
    offline = RhinoSoftwareConnection(None, software_running=lambda: None)
    assert (await offline.ensure_connection(1)).reason == "softwareNotRunning"
    missing = RhinoSoftwareConnection(None, software_running=lambda: "si_fixture")
    assert (await missing.ensure_connection(1)).reason == "nativeComponentUnavailable"


@pytest.mark.asyncio
async def test_connected_bridge_is_confirmed_without_engineering_dispatch():
    with FakeBridge() as fake:
        bridge = BridgeClient(bridge_config(fake.endpoint))
        adapter = RhinoSoftwareConnection(bridge, software_running=lambda: "si_fixture")
        try:
            assert (await adapter.ensure_connection(1)).outcome == "confirmed"
            assert len(fake.connection_probes) == 1
            assert fake.command_frames == []
        finally:
            bridge.disconnect()


@pytest.mark.asyncio
async def test_busy_bridge_does_not_interrupt_or_open_another_session():
    class BusyBridge:
        def confirm_connection(self, _timeout):
            raise BridgeBusy("busy")

    adapter = RhinoSoftwareConnection(BusyBridge(), software_running=lambda: "si_fixture")
    assert (await adapter.ensure_connection(1)).outcome == "deferred"


@pytest.mark.asyncio
async def test_replaced_native_peer_cannot_inherit_in_flight_success():
    started, finish = threading.Event(), threading.Event()

    class SlowBridge:
        def confirm_connection(self, _timeout):
            started.set()
            finish.wait(2)

    adapter = RhinoSoftwareConnection(SlowBridge(), software_running=lambda: "si_fixture")
    pending = asyncio.create_task(adapter.ensure_connection(1))
    try:
        while not started.is_set():
            await asyncio.sleep(0.001)
        adapter.set_bridge(None)
        finish.set()
        assert (await pending).outcome == "unconfirmed"
        assert (await adapter.ensure_connection(1)).reason == "nativeComponentUnavailable"
    finally:
        finish.set()


@pytest.mark.asyncio
async def test_new_native_peer_waits_for_explicit_connection_request():
    class Bridge:
        calls = 0

        def confirm_connection(self, _timeout):
            self.calls += 1

    bridge = Bridge()
    created = []
    def factory(_peer):
        created.append(True)
        return bridge
    adapter = RhinoSoftwareConnection(None, software_running=lambda: "si_fixture", peer_factory=factory)
    peer = {"softwareInstanceId": "si_fixture", "generation": 1}
    adapter.update_native_peer(peer)
    adapter.update_native_peer(peer)
    assert len(created) == 1
    assert bridge.calls == 0
    assert (await adapter.ensure_connection(1)).outcome == "confirmed"
    assert bridge.calls == 1
