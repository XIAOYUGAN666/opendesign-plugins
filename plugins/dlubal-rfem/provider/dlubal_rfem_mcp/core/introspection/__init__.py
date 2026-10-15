"""Dlubal API introspection — deterministic, version-exact discovery over the installed package.

Backs the `inspect_rfem_api` MCP tool.
Pure reflection on `dlubal.api.<product>` protobuf descriptors; no connection to the
application is required, so it answers while the product is closed. The names returned
are exactly what that product's `run_<product>_script` tool accepts.

Call as `inspect(product, query)` where product is "rfem";
one index is built and cached per product on first use.
"""

from dlubal_rfem_mcp.core.introspection.resolve import inspect

__all__ = ["inspect"]
