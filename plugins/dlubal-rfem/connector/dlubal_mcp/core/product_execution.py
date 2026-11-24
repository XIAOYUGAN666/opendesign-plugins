"""Durable script workers for products sharing the Dlubal application API."""

import importlib
import logging
from contextlib import nullcontext
from pathlib import Path

from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolResult, TextContent

from dlubal_mcp.core import rpc_deadline
from dlubal_mcp.core.off_loop import off_event_loop
from dlubal_mcp.core.sandbox import execute_script, script_builtins, validate_code_ast
from dlubal_mcp.rfem.execution import (
    ExecutionManager,
    default_state_path,
    fingerprint,
    result_text,
)
from dlubal_mcp.rfem.model_binding import bound_connection, canonical_guid

logger = logging.getLogger("dlubal_mcp.execution")


class ExecutionResult(ToolResult):
    def to_mcp_result(self):
        return CallToolResult(
            content=self.content,
            structuredContent=self.structured_content,
            isError=self.structured_content["state"] != "succeeded",
        )


def execution_response(result, product="rfem"):
    return ExecutionResult(
        content=[TextContent(type="text", text=result_text(result, product))],
        structured_content=result,
    )


def read_application_info(session, product, control=None) -> dict:
    """The answer of get_<product>_application_info, within a time limit.

    This is the Connector's readiness probe. Without a limit, an application
    that never answers kept this read, and with it the product's session,
    busy for good.
    """
    seconds = rpc_deadline.APPLICATION_INFO_SECONDS
    try:
        with (
            rpc_deadline.time_limit(seconds),
            control.reading() if control else nullcontext(),
            session.connection() as app,
        ):
            info = app.get_application_info()
            return {"status": "ok", "version": str(info.version)[:40]}
    except Exception as exc:
        if rpc_deadline.is_deadline_exceeded(exc):
            raise RuntimeError(rpc_deadline.no_answer_message(product, seconds)) from exc
        raise


def register_application_info(mcp, session, product, control=None) -> str:
    """Register read-only get_<product>_application_info.

    The same contract as get_rfem_application_info: prove that the product's API
    answers and read its version, with no script, no model, no execution record
    and no license identity. A launcher needs exactly this to confirm a working
    connection; before 1.0.18 RSTAB and RSECTION could only be probed through
    run_<product>_script, which carries script, journal and approval semantics.
    """
    name = f"get_{product}_application_info"

    @off_event_loop
    def get_application_info() -> dict:
        return read_application_info(session, product, control)

    mcp.tool(
        name=name,
        annotations={"readOnlyHint": True},
        description=(
            f"Verify the live {product.upper()} API connection and read its version. "
            "No model or Python script is required. Does not disclose license identities."
        ),
    )(get_application_info)
    return name


def register_script_tools(mcp, session, config, product, control=None):
    state_file = getattr(config, "execution_state_file", "")
    path = (
        Path(state_file)
        if state_file
        else default_state_path().with_name(f"{product}-executions.sqlite3")
    )
    manager = ExecutionManager(
        path,
        endpoint=f"{config.url}:{config.api_port}",
        control=control,
        product=product,
    )

    @off_event_loop
    def run_script(
        code: str,
        execution_id: str | None = None,
        context_id: str = "",
        model_guid: str | None = None,
    ) -> ToolResult:
        error = validate_code_ast(
            code, available_objects=f"app, {product}, common, math"
        )
        if error:
            return execution_response({"state": "rejected", "error": error}, product)
        try:
            if model_guid:
                model_guid = canonical_guid(model_guid)

            def work(capture, record):
                from dlubal.api import common

                module = importlib.import_module(f"dlubal.api.{product}")
                # Connection close and its lock both belong to the worker. A
                # returned wait timeout never ends this context prematurely.
                with bound_connection(
                    session,
                    common,
                    model_guid or manager.binding(context_id),
                    record,
                    capture.print,
                ) as bound:
                    namespace = {
                        "__builtins__": script_builtins(capture.print),
                        product: module,
                        "common": common,
                        "app": bound,
                        "math": importlib.import_module("math"),
                    }
                    execute_script(code, namespace)

            result = manager.submit(
                execution_id, context_id, fingerprint(code, model_guid), work
            )
        except Exception as exc:
            logger.debug("Product script admission failed", exc_info=True)
            result = {"state": "rejected", "error": str(exc)}
        return execution_response(result, product)

    @off_event_loop
    def get_execution(execution_id: str) -> ToolResult:
        return execution_response(manager.status(execution_id), product)

    mcp.tool(
        name=f"run_{product}_script",
        description=(
            f"Execute an authorized {product.upper()} script with durable operation identity and model binding. "
            "Caller-owned execution_id is a UUID; changed input for the same ID is rejected. "
            "A running or unknown result is not completion or cancellation. Query the status tool by ID; "
            "never replay to recover output. Earlier model changes are not rolled back on failure. "
            f"Available objects: app, {product}, common, math. Inspect the installed API before writing code."
        ),
    )(run_script)
    mcp.tool(
        name=f"get_{product}_execution",
        annotations={"readOnlyHint": True},
        description="Read operation status and bounded output without connecting to the application or replaying code.",
    )(get_execution)
    return [f"run_{product}_script", f"get_{product}_execution"]
