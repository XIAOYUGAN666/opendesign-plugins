"""Structured JSON logging and audit trail."""

import json
import logging
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        for key in ("api_key", "tool_name", "duration_ms", "success", "client_ip"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_output: bool = True, stream=None) -> None:
    """Install the root log handler. `stream` defaults to stdout.

    The stdio transport owns stdout: it is the JSON-RPC frame, and the spec is
    explicit that a server "MUST NOT write anything to its stdout that is not a
    valid MCP message" while stderr is the sanctioned logging channel. A single
    INFO line on stdout (e.g. "RFEM tools registered.") corrupts the stream and
    the client fails during initialize. __main__ therefore passes sys.stderr on
    the stdio path; every HTTP path keeps stdout unchanged.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log_tool_call(tool_name: str, api_key: str, duration_ms: float, success: bool, client_ip: str = "") -> None:
    """Emit an audit log entry for a tool call. Does not log code content."""
    logger = logging.getLogger("dlubal_mcp.audit")
    masked = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "***"
    logger.info(
        "Tool call: %s by %s (%.0fms, %s)",
        tool_name, masked, duration_ms, "ok" if success else "error",
        extra={
            "tool_name": tool_name,
            "api_key": masked,
            "duration_ms": round(duration_ms),
            "success": success,
            "client_ip": client_ip,
        },
    )
