"""Dlubal API introspection — deterministic, version-exact discovery over the installed package.

Backs the `inspect_rfem_api`, `inspect_rstab_api` and `inspect_rsection_api` MCP tools.
Pure reflection on `dlubal.api.<product>` protobuf descriptors; no connection to the
application is required, so it answers while the product is closed. The names returned
are exactly what that product's `run_<product>_script` tool accepts.

Call as `inspect(product, query)` where product is "rfem", "rstab" or "rsection";
one index is built and cached per product on first use.
"""

from dlubal_mcp.core.introspection.resolve import inspect

__all__ = ["inspect"]
