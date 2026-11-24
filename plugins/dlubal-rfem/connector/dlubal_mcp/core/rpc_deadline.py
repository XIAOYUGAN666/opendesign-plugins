"""Time limits for the read-only calls a tool makes to a Dlubal application.

dlubal.api has no timeout parameter. A call to an application that accepts the
connection but never answers (a modal dialog, a suspended or hung process)
waits forever. The waiting thread holds the product's session lock, so every
later call is refused as busy, and the operation control keeps reporting a
read in progress, so the Connector cannot drain and restart the server either.

A read-only tool therefore runs inside ``time_limit(seconds)``: every gRPC call
it makes, the connection check inside ``Application()`` included, carries the
remaining time as its deadline. Scripts, writes and calculations deliberately
get none. A calculation can run for an hour, and a write stopped midway would
leave its outcome unknown; off_loop keeps the server answering while they run.

The deadline is attached where dlubal.api builds its channel. Each product's
``application`` module calls ``init_channel`` by its module-global name in
every build checked (RFEM 6.11, 6.15, 6.16; RSTAB 9.12, 9.15, 9.16; RSECTION
1.15, 1.16), and ``install`` wraps that name. A build without it is left
untouched with one warning, and its calls run without a limit, exactly as
before this module.

grpc is imported only once a channel is built: it arrives with dlubal.api and
is not a dependency of this server, which starts without any product API.
"""

import collections
import contextvars
import functools
import logging
import sys
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger("dlubal_mcp.core.rpc_deadline")

# Long enough for an API session start that waits on the license server, short
# enough that a probe never holds the product's session for long. The
# Connector's readiness probe gives up after 8 s; this only frees the server.
APPLICATION_INFO_SECONDS = 20.0
# Below CALC's 150 s tool-call timeout, so CALC receives this answer and not
# its own timeout.
RESULT_READ_SECONDS = 120.0

# A deadline already past still has to reach gRPC as a positive timeout.
_EXPIRED_TIMEOUT = 0.001

_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "dlubal_mcp_rpc_deadline", default=None
)
_MARK = "_dlubal_mcp_rpc_deadline"
_install_lock = threading.Lock()
_warned: set[str] = set()


@contextmanager
def time_limit(seconds: float):
    """Bound every gRPC call made in this context; a nested limit never extends it."""
    deadline = time.monotonic() + seconds
    current = _deadline.get()
    token = _deadline.set(deadline if current is None else min(current, deadline))
    try:
        yield
    finally:
        _deadline.reset(token)


def _bounded(details, call_details_type):
    deadline = _deadline.get()
    if deadline is None or details.timeout is not None:
        return details
    return call_details_type(
        details.method,
        max(deadline - time.monotonic(), _EXPIRED_TIMEOUT),
        details.metadata,
        details.credentials,
        getattr(details, "wait_for_ready", None),
        getattr(details, "compression", None),
    )


@functools.cache
def _interceptor():
    import grpc

    class CallDetails(
        collections.namedtuple(
            "CallDetails",
            (
                "method",
                "timeout",
                "metadata",
                "credentials",
                "wait_for_ready",
                "compression",
            ),
        ),
        grpc.ClientCallDetails,
    ):
        pass

    class DeadlineInterceptor(
        grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor
    ):
        def intercept_unary_unary(self, continuation, client_call_details, request):
            return continuation(_bounded(client_call_details, CallDetails), request)

        def intercept_unary_stream(self, continuation, client_call_details, request):
            return continuation(_bounded(client_call_details, CallDetails), request)

    return DeadlineInterceptor()


def install(application_cls) -> bool:
    """Make the module that defines application_cls build deadline-aware channels.

    Idempotent and cheap: sessions call it before every connection. Returns
    whether time limits apply to that product's calls.
    """
    module_name = getattr(application_cls, "__module__", "")
    module = sys.modules.get(module_name)
    original = getattr(module, "init_channel", None)
    if getattr(original, _MARK, False):
        return True
    if not callable(original):
        if module_name not in _warned:
            _warned.add(module_name)
            logger.warning(
                "%s has no init_channel; calls to this application run without a time limit.",
                module_name,
            )
        return False
    with _install_lock:
        original = module.init_channel
        if getattr(original, _MARK, False):
            return True

        @functools.wraps(original)
        def init_channel(*args, **kwargs):
            channel = original(*args, **kwargs)
            if channel is None:
                return channel
            import grpc

            return grpc.intercept_channel(channel, _interceptor())

        setattr(init_channel, _MARK, True)
        module.init_channel = init_channel
    return True


def _chain(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def is_deadline_exceeded(exc: BaseException) -> bool:
    """True when exc, or what it wraps, is a gRPC deadline that ran out.

    Application() re-raises a failed connection check as a RuntimeError whose
    text carries the status name, with the RpcError suppressed.
    """
    for item in _chain(exc):
        code = getattr(item, "code", None)
        if callable(code):
            # The status of a failed call can itself fail to read.
            try:
                if getattr(code(), "name", "") == "DEADLINE_EXCEEDED":
                    return True
            except Exception:
                logger.debug("gRPC status unavailable", exc_info=True)
        if "DEADLINE_EXCEEDED" in str(item):
            return True
    return False


def no_answer_message(product: str, seconds: float) -> str:
    label = product.upper()
    return (
        f"{label} did not answer within {seconds:g} s. An open dialog in {label}, "
        f"or {label} still busy with another task, can cause this. Check {label}, then try again."
    )
