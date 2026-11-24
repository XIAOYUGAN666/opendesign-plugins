"""Entry point: python -m dlubal_mcp  or  dlubal-mcp CLI."""

import argparse
import os
import sys


def _parse_args():
    parser = argparse.ArgumentParser(
        prog="dlubal-mcp",
        description="Unified Dlubal MCP server (RFEM, RSTAB, RSECTION)",
    )
    parser.add_argument(
        "--host", default=None, help="Bind address (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Listen port (default: 7130, env: DLUBAL_MCP_PORT)",
    )
    parser.add_argument(
        "--transport",
        default=None,
        choices=["streamable-http", "sse", "stdio"],
        help="MCP transport (default: streamable-http). Use stdio for a local "
        "client that launches this server itself: no port, no access token.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Dlubal gRPC API key value (skips config.ini lookup)",
    )
    parser.add_argument(
        "--key-name", default=None, help="Dlubal API key name to load from config.ini"
    )
    parser.add_argument(
        "--config-path",
        default=None,
        help="Path to directory containing config.ini (default: dlubal/api)",
    )
    parser.add_argument(
        "--require-auth",
        action="store_true",
        help="Require Bearer token auth on all MCP requests",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=None,
        help="Max requests per minute per API key",
    )
    parser.add_argument(
        "--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args, _ = parser.parse_known_args()
    return args


def _run_stdio(settings) -> None:
    """Serve MCP over stdin/stdout, with no HTTP surface whatsoever.

    The client launches this process and is the only party that can speak to it,
    so there is nothing to authenticate: no socket, no anonymous caller, no
    token to distribute. validate_http_security() is reached only from inside
    create_asgi_app(), which this path never calls — so the bearer middleware,
    the rate limiter, the loopback-bind check and the /health, /auth-check and
    /control routes are absent by construction rather than by a flag.
    """
    from dlubal_mcp.core.server import build_stdio_mcp

    # show_banner=False: FastMCP's startup banner is decoration this transport
    # cannot afford to have pointed at the wrong stream.
    build_stdio_mcp(settings).run(transport="stdio", show_banner=False)


def main() -> None:
    args = _parse_args()

    # Propagate CLI overrides to env before settings are loaded
    if args.api_key:
        os.environ["DLUBAL_API_KEY"] = args.api_key
    if args.key_name:
        os.environ.setdefault("RFEM_API_KEY_NAME", args.key_name)
        os.environ.setdefault("RSTAB_API_KEY_NAME", args.key_name)
        os.environ.setdefault("RSECTION_API_KEY_NAME", args.key_name)
    if args.config_path:
        os.environ["DLUBAL_CONFIG_INI_PATH"] = args.config_path
    if args.require_auth:
        os.environ["DLUBAL_MCP_REQUIRE_AUTH"] = "true"
    if args.rate_limit:
        os.environ["DLUBAL_MCP_RATE_LIMIT_RPM"] = str(args.rate_limit)
    if args.log_level:
        os.environ["DLUBAL_MCP_LOG_LEVEL"] = args.log_level

    from dlubal_mcp.core.config import DlubalMCPSettings
    from dlubal_mcp.core.logging_config import setup_logging

    settings = DlubalMCPSettings()

    host = args.host or settings.host
    port = args.port or settings.port
    transport = args.transport or settings.transport

    # Resolved BEFORE logging is configured: on stdio the root handler must not
    # write to stdout, and product imports below already log at INFO.
    setup_logging(
        level=settings.log_level,
        json_output=settings.log_json,
        stream=sys.stderr if transport == "stdio" else None,
    )

    if transport == "stdio":
        _run_stdio(settings)
        return

    # Allow transport override via the settings object for the ASGI builder
    settings.transport = transport
    settings.host = host

    import uvicorn

    from dlubal_mcp.core.server import create_asgi_app

    app = create_asgi_app(settings)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
