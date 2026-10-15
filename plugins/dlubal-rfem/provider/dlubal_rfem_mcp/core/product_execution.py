"""Durable script workers for products sharing the Dlubal application API."""

import importlib
import logging
from pathlib import Path

from fastmcp.tools import ToolResult
from mcp.types import CallToolResult, TextContent

from dlubal_rfem_mcp.core.sandbox import (
    execute_script,
    script_builtins,
    validate_code_ast,
)
from dlubal_rfem_mcp.rfem.execution import (
    ExecutionManager,
    default_state_path,
    fingerprint,
    result_text,
)
from dlubal_rfem_mcp.rfem.model_binding import bound_connection, canonical_guid

logger = logging.getLogger("dlubal_rfem_mcp.execution")


class ExecutionResult(ToolResult):
    def to_mcp_result(self):
        if self.structured_content is None:
            raise ValueError("executionResultMissing")
        return CallToolResult(
            content=self.content,
            structured_content=self.structured_content,
            is_error=self.structured_content["state"] != "succeeded",
        )


def execution_response(result):
    return ExecutionResult(
        content=[TextContent(type="text", text=result_text(result))],
        structured_content=result,
    )


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
            return execution_response({"state": "rejected", "error": error})
        try:
            if model_guid:
                model_guid = canonical_guid(model_guid)

            def work(capture, record):
                from dlubal.api import common

                module = importlib.import_module(f"dlubal.api.{product}")
                # Connection close and its lock both belong to the worker. A
                # returned wait timeout never ends this context prematurely.
                with bound_connection(
                    session, common, model_guid or manager.binding(context_id), record
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
        return execution_response(result)

    def get_execution(execution_id: str) -> ToolResult:
        return execution_response(manager.status(execution_id))

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
