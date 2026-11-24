"""Time limits on read-only calls to an application that never answers.

The fake application below is built like dlubal.api's: its Application looks
up init_channel as a module global, wraps the channel in an auth interceptor
that rebuilds the call details (keeping only method, timeout, metadata and
credentials), and checks the connection with one call in its constructor.
"""

import sys
import threading
import time
import types
from concurrent import futures
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from dlubal_mcp.core import rpc_deadline
from dlubal_mcp.core.product_execution import read_application_info
from dlubal_mcp.rfem.results import _safe_error_code

METHOD = "/dlubal.test.Application/GetApplicationInfo"

APPLICATION_SOURCE = """
import grpc

class _ClientCallDetails(grpc.ClientCallDetails):
    def __init__(self, call_details, metadata):
        self.method = call_details.method
        self.timeout = call_details.timeout
        self.credentials = call_details.credentials
        self.metadata = metadata

class AuthInterceptor(grpc.UnaryUnaryClientInterceptor):
    def intercept_unary_unary(self, continuation, client_call_details, request):
        details = _ClientCallDetails(client_call_details, [("authorization", "Bearer test")])
        return continuation(details, request)

def init_channel(url, port):
    return grpc.intercept_channel(grpc.insecure_channel(f"{url}:{port}"), AuthInterceptor())

class Application:
    def __init__(self, url, port):
        self.channel = init_channel(url, port)
        self._info = self.channel.unary_unary(METHOD)
        self.get_application_info()

    def get_application_info(self, timeout=None):
        return self._info(b"", timeout=timeout)

    def close_connection(self):
        self.channel.close()
"""


@pytest.fixture
def grpc():
    # grpcio arrives with dlubal.api; the server itself does not depend on it.
    return pytest.importorskip("grpc")


@pytest.fixture
def silent_application(grpc, monkeypatch):
    """A gRPC endpoint that accepts every call and answers none until released."""
    monkeypatch.setenv("no_grpc_proxy", "127.0.0.1")
    release = threading.Event()

    def never_answers(request, context):
        release.wait(30)
        return b"6.16"

    handler = grpc.method_handlers_generic_handler(
        "dlubal.test.Application",
        {
            "GetApplicationInfo": grpc.unary_unary_rpc_method_handler(never_answers),
        },
    )
    endpoint = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    endpoint.add_generic_rpc_handlers((handler,))
    port = endpoint.add_insecure_port("127.0.0.1:0")
    endpoint.start()
    try:
        yield port, release
    finally:
        release.set()
        endpoint.stop(0)


@pytest.fixture
def application_module(grpc, monkeypatch):
    module = types.ModuleType("dlubal_test_api.application")
    module.METHOD = METHOD
    exec(APPLICATION_SOURCE, module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def test_a_limited_connection_check_ends_at_its_deadline(
    grpc, silent_application, application_module
):
    port, _ = silent_application
    assert rpc_deadline.install(application_module.Application)
    started = time.monotonic()
    with pytest.raises(grpc.RpcError) as raised, rpc_deadline.time_limit(0.5):
        application_module.Application("127.0.0.1", port)
    assert time.monotonic() - started < 5
    assert raised.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    assert rpc_deadline.is_deadline_exceeded(raised.value)


def test_unlimited_calls_still_wait_for_the_application(
    silent_application, application_module
):
    # Scripts, writes and calculations stay unlimited: a calculation can take
    # an hour. Only the reads that opt in get a deadline.
    port, release = silent_application
    rpc_deadline.install(application_module.Application)
    answered = []
    caller = threading.Thread(
        target=lambda: answered.append(
            application_module.Application("127.0.0.1", port).get_application_info()
        ),
        daemon=True,
    )
    caller.start()
    caller.join(1.5)
    assert caller.is_alive()
    release.set()
    caller.join(10)
    assert answered == [b"6.16"]


def test_an_explicit_timeout_of_the_caller_is_kept(
    grpc, silent_application, application_module
):
    port, release = silent_application
    rpc_deadline.install(application_module.Application)
    with rpc_deadline.time_limit(30):
        # The constructor's check must be answered to get an Application at all.
        release.set()
        app = application_module.Application("127.0.0.1", port)
        release.clear()
        started = time.monotonic()
        with pytest.raises(grpc.RpcError):
            app.get_application_info(timeout=0.3)
        assert time.monotonic() - started < 5
        app.close_connection()


def test_a_nested_limit_never_extends_the_outer_one(
    grpc, silent_application, application_module
):
    port, _ = silent_application
    rpc_deadline.install(application_module.Application)
    started = time.monotonic()
    with (
        pytest.raises(grpc.RpcError),
        rpc_deadline.time_limit(0.5),
        rpc_deadline.time_limit(60),
    ):
        application_module.Application("127.0.0.1", port)
    assert time.monotonic() - started < 5


def test_install_is_idempotent(grpc, application_module):
    assert rpc_deadline.install(application_module.Application)
    wrapped = application_module.init_channel
    assert rpc_deadline.install(application_module.Application)
    assert application_module.init_channel is wrapped


def test_a_build_without_init_channel_runs_unlimited_with_one_warning(
    monkeypatch, caplog
):
    module = types.ModuleType("dlubal_test_api.future_build")

    class Application:
        pass

    Application.__module__ = module.__name__
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with caplog.at_level("WARNING", logger="dlubal_mcp.core.rpc_deadline"):
        assert not rpc_deadline.install(Application)
        assert not rpc_deadline.install(Application)
    assert len(caplog.records) == 1


@pytest.mark.parametrize("product", ["rfem"])
def test_the_probe_names_what_to_check_when_the_application_never_answers(product):
    class Session:
        @contextmanager
        def connection(self):
            # What dlubal.api raises when its connection check runs out of time.
            raise RuntimeError(
                "gRPC connection check failed (DEADLINE_EXCEEDED) for '127.0.0.1:9000'."
            )
            yield

    with pytest.raises(RuntimeError) as raised:
        read_application_info(Session(), product)
    message = str(raised.value)
    assert message.startswith(f"{product.upper()} did not answer within 20 s")
    assert "dialog" in message


def test_the_probe_runs_under_the_application_info_limit(monkeypatch):
    seen = []

    class Session:
        @contextmanager
        def connection(self):
            seen.append(rpc_deadline._deadline.get() - time.monotonic())
            yield SimpleNamespace(
                get_application_info=lambda: SimpleNamespace(version="6.16.0004")
            )

    assert read_application_info(Session(), "rfem") == {
        "status": "ok",
        "version": "6.16.0004",
    }
    assert 0 < seen[0] <= rpc_deadline.APPLICATION_INFO_SECONDS
    assert rpc_deadline._deadline.get() is None


def test_a_result_read_that_runs_out_of_time_reports_rfem_timeout():
    assert (
        _safe_error_code(RuntimeError("gRPC API call failed (DEADLINE_EXCEEDED)"))
        == "rfem_timeout"
    )
    assert _safe_error_code(RuntimeError("Cannot connect")) == "rfem_unavailable"
