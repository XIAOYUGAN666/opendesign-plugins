"""The client application a tool call runs on behalf of.

CALC sends its signed identity on every tools/call as two HTTP headers (Id and
signature). dlubal-mcp does not interpret them: it hands both to dlubal.api's
Application, which sends them to RFEM/RSTAB/RSECTION as gRPC metadata; the product
passes them to the license server, which verifies the signature (seeded secret
known only to CALC and the license server) and records the application on the API
session.

Two further, unsigned headers are optional: the application's name, which the
product only logs, and CALC's session (chat) ID. Each goes along with the identity
only, and only to a dlubal.api that takes it; one that predates them still gets
the identity.
"""

import inspect
import logging

from fastmcp.server.dependencies import get_http_headers

logger = logging.getLogger("dlubal_mcp.core.client_application")

# HTTP header (lower case, as get_http_headers returns it) -> Application parameter.
_IDENTITY_PARAMETERS = {
    "x-dlubal-client-application-id": "client_application_id",
    "x-dlubal-client-application-signature": "client_application_signature",
}
_OPTIONAL_PARAMETERS = {
    "x-dlubal-client-application-name": "client_application_name",
    "x-dlubal-client-application-session-id": "client_application_session_id",
}


def _carriable(value: str) -> bool:
    """Non-empty and something gRPC metadata can carry."""
    return bool(value) and value.isascii() and value.isprintable()


def current_client_application() -> dict[str, str]:
    """All values sent with the MCP request being handled; {} unless the Id and the
    signature are there.

    Read from mcp's per-message request context, not from anything set per HTTP
    connection: with stateful streamable-http the session-creating request is not the
    tool call's. {} outside an HTTP request (stdio, background work). A value gRPC
    metadata cannot carry is dropped rather than failing the product connection; an
    optional one is dropped alone.
    """
    headers = get_http_headers()
    values = {
        parameter: headers.get(header, "")
        for header, parameter in _IDENTITY_PARAMETERS.items()
    }
    if not all(_carriable(value) for value in values.values()):
        return {}
    for header, parameter in _OPTIONAL_PARAMETERS.items():
        value = headers.get(header, "")
        if _carriable(value):
            values[parameter] = value
    return values


# dlubal-mcp and the dlubal.api bundled with RFEM release independently, so an
# Application without the parameters is the normal state after this ships; passing
# them anyway would raise TypeError and fail every tool call. Read once per class.
_application_parameters: dict[type, frozenset[str]] = {}


def client_application_kwargs(application_cls: type) -> dict:
    """kwargs declaring the client application to Application(...); {} when there is
    none, or when the installed dlubal.api predates the Id and the signature. The
    name and the session ID are left out for a dlubal.api that does not take them."""
    values = current_client_application()
    if not values:
        return {}
    parameters = _application_parameters.get(application_cls)
    if parameters is None:
        parameters = frozenset(inspect.signature(application_cls.__init__).parameters)
        _application_parameters[application_cls] = parameters
        missing = [p for p in _IDENTITY_PARAMETERS.values() if p not in parameters]
        if missing:
            logger.warning(
                "%s has no %s parameter(s) - the installed dlubal.api predates them; "
                "the client application is not reported to the license server.",
                application_cls.__module__,
                "/".join(missing),
            )
    if not parameters.issuperset(_IDENTITY_PARAMETERS.values()):
        return {}
    return {name: value for name, value in values.items() if name in parameters}
