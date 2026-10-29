"""Control transport is generated once and does not duplicate plugin policy."""
import asyncio
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConnectorChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_and_cancellation_have_separate_operation_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b = root / "a.py", root / "b.py"
            subprocess.run([sys.executable, "-B", str(ROOT / "scripts/generate_connector_channel.py"), str(a), str(b)], check=True)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            package = types.ModuleType("temporary_connector")
            package.__path__ = [str(root)]
            sys.modules[package.__name__] = package
            codec = types.ModuleType("temporary_connector._connector_control")
            codec.decode_control = lambda raw: json.loads(raw)
            codec.validate_event = lambda event: event
            codec.AdapterConnectionResult = types.SimpleNamespace
            sys.modules[codec.__name__] = codec
            spec = importlib.util.spec_from_file_location("temporary_connector.channel", a)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            emitted = []
            gate = asyncio.Event()

            class Adapter:
                def __init__(self):
                    self.peers = []

                async def ensure_connection(self, deadline_seconds):
                    await gate.wait()
                    return types.SimpleNamespace(outcome="confirmed", reason="none", software_instance_id="si_fixture")

                def update_native_peer(self, peer):
                    self.peers.append(peer)

            adapter = Adapter()
            channel = module.ConnectorChannel({"generation": 1}, adapter, emitted.append)
            def frame(kind, operation):
                return json.dumps({"kind":kind, "operationId":operation, "binding":{"generation":1}, "deadlineMs":15000}).encode()
            await channel.accept(frame("ensureSoftwareConnection", "first"))
            await asyncio.sleep(0)
            await channel.accept(frame("cancelConnectionCheck", "first"))
            gate.set()
            await asyncio.sleep(0)
            self.assertEqual(emitted, [])
            await channel.accept(frame("ensureSoftwareConnection", "second"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(emitted[0]["operationId"], "second")
            self.assertEqual(emitted[0]["sequence"], 1)
            self.assertEqual(emitted[0]["outcome"], "confirmed")
            channel.publish_change("blocked", "softwareNotRunning")
            self.assertEqual(emitted[1]["kind"], "softwareConnectionChanged")
            self.assertEqual(emitted[1]["sequence"], 2)
            await channel.accept(json.dumps({"kind":"nativePeerChanged", "binding":{"generation":1}, "peerRevision":1, "peer":None}).encode())
            self.assertEqual(adapter.peers, [None])
            self.assertEqual(len(emitted), 2)
            with self.assertRaises(ValueError):
                await channel.accept(json.dumps({"kind":"nativePeerChanged", "binding":{"generation":1}, "peerRevision":1, "peer":None}).encode())
            await channel.close()
            oversized = module.ConnectorChannel({"generation": 1}, Adapter(), emitted.append)
            with self.assertRaisesRegex(ValueError, "invalidFrame"):
                await oversized.run_reader(io.BytesIO(b"x" * (16 * 1024 + 1) + b"\n"))
            del sys.modules[codec.__name__]
            del sys.modules[package.__name__]
