"""Verify the RFEM connection diagnostic against an isolated local gRPC peer.

The peer uses an ephemeral loopback port, a synthetic key and an in-memory SDK
configuration.  No RFEM process, user credential or user INI is consulted.
"""

import asyncio
import inspect
import os
import unittest
from concurrent import futures
from pathlib import Path
from unittest.mock import patch

import grpc
from dlubal.api.common import connection as sdk_connection
from dlubal.api.common.common_messages_pb2 import ApplicationInfo
from dlubal.api.rfem import application_pb2_grpc
from fastmcp import Client, FastMCP
from google.protobuf.empty_pb2 import Empty

from dlubal_rfem_mcp.rfem.model_operations import register_model_operations
from dlubal_rfem_mcp.rfem.session import RFEMSession
from dlubal_rfem_mcp.rfem.software_connection import RFEMSoftwareConnection


class _MemoryConfig:
    def get_key(self, _value):
        return None


class _Audit(grpc.ServerInterceptor):
    def __init__(self):
        self.methods = []

    def intercept_service(self, continuation, handler_call_details):
        self.methods.append(handler_call_details.method.rsplit("/", 1)[-1])
        return continuation(handler_call_details)


class _Application(application_pb2_grpc.ApplicationServicer):
    def __init__(self, scenario):
        self.scenario = scenario

    def get_application_info(self, request, context):
        if self.scenario == "authentication":
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid api key")
        if self.scenario == "unavailable":
            context.abort(grpc.StatusCode.UNAVAILABLE, "synthetic unavailable")
        if self.scenario == "timeout":
            context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "synthetic timeout")
        build = "6.14.0013.248" if self.scenario == "wrong-build" else "6.14.0013.249"
        return ApplicationInfo(
            name="RFEM fixture", version="6.14.0013", full_version=build
        )

    def close_connection(self, request, context):
        if self.scenario == "close-failure":
            context.abort(grpc.StatusCode.UNAVAILABLE, "synthetic close failure")
        return Empty()


class ConnectionServiceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_unified_connection_adapter_uses_only_service_methods(self):
        audit = _Audit()
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2), interceptors=[audit])
        application_pb2_grpc.add_ApplicationServicer_to_server(_Application("success"), server)
        port = server.add_insecure_port("127.0.0.1:0")
        self.assertGreater(port, 0)
        self.assertNotEqual(port, 9000)
        server.start()
        try:
            session = RFEMSession("synthetic-key", "127.0.0.1", port, expected_build="6.14.0013.249")
            adapter = RFEMSoftwareConnection(session, software_running=lambda: "si_fixture")
            with (patch("dlubal.api.common.connection._config", _MemoryConfig), patch.object(sdk_connection, "options", [*sdk_connection.options, ("grpc.enable_http_proxy", 0)])):
                result = await adapter.ensure_connection(10)
            self.assertEqual(result.outcome, "confirmed")
            self.assertTrue(audit.methods)
            self.assertLessEqual(set(audit.methods), {"get_application_info", "close_connection"})
        finally:
            server.stop(0).wait(5)

    def test_audited_modules_come_from_expected_artifact(self):
        expected = os.environ.get("RFEM_AUDIT_EXPECTED_ROOT")
        if expected is None:
            self.skipTest("artifact origin is asserted in the installed-artifact run")
        root = Path(expected) / "runtime" / "python" / "Lib" / "site-packages"
        self.assertTrue(
            os.path.samefile(
                inspect.getfile(RFEMSession),
                root / "dlubal_rfem_mcp" / "rfem" / "session.py",
            )
        )
        self.assertTrue(
            os.path.samefile(
                inspect.getfile(register_model_operations),
                root / "dlubal_rfem_mcp" / "rfem" / "model_operations.py",
            )
        )
        from dlubal.api.rfem.application import Application

        self.assertTrue(
            os.path.samefile(
                inspect.getfile(Application),
                root / "dlubal" / "api" / "rfem" / "application.py",
            )
        )

    async def _diagnostic(self, scenario):
        audit = _Audit()
        server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=2), interceptors=[audit]
        )
        application_pb2_grpc.add_ApplicationServicer_to_server(
            _Application(scenario), server
        )
        port = server.add_insecure_port("127.0.0.1:0")
        self.assertGreater(port, 0)
        self.assertNotEqual(
            port, 9000, "fixture must not contact the real RFEM endpoint"
        )
        server.start()
        try:
            session = RFEMSession(
                "synthetic-key", "127.0.0.1", port, expected_build="6.14.0013.249"
            )
            mcp = FastMCP("service-contract-fixture")
            register_model_operations(mcp, session, manager=None)
            # A machine-wide proxy must never receive this synthetic local call.
            # Preserve the SDK's options and disable proxy only in this test.
            with (
                patch("dlubal.api.common.connection._config", _MemoryConfig),
                patch.object(
                    sdk_connection,
                    "options",
                    [*sdk_connection.options, ("grpc.enable_http_proxy", 0)],
                ),
            ):
                async with Client(mcp) as client:
                    result = await asyncio.wait_for(
                        client.call_tool(
                            "get_rfem_application_info", {}, raise_on_error=False
                        ),
                        timeout=10,
                    )
            self.assertTrue(audit.methods)
            self.assertLessEqual(
                set(audit.methods), {"get_application_info", "close_connection"}
            )
            return result, audit.methods
        finally:
            server.stop(0).wait(5)

    async def test_success_uses_only_service_methods(self):
        result, methods = await self._diagnostic("success")
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "ok")
        self.assertEqual(methods.count("get_application_info"), 3)
        self.assertEqual(methods.count("close_connection"), 1)

    async def test_refusal_and_cleanup_paths_use_only_service_methods(self):
        for scenario in (
            "authentication",
            "unavailable",
            "timeout",
            "wrong-build",
            "close-failure",
        ):
            with self.subTest(scenario=scenario):
                result, _methods = await self._diagnostic(scenario)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["status"], "error")


if __name__ == "__main__":
    unittest.main()
