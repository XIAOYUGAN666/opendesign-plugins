"""Rhino integration through the Model Context Protocol."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("rhino-mcp-provider")
except PackageNotFoundError:
    # Editable/source checkouts without metadata fall back to "0+unknown" rather
    # than a hardcoded value that would silently drift from pyproject.toml.
    __version__ = "0+unknown"

# Expose key classes and functions for easier imports.
# The server owns the explicit ordered tool registry. Tools import its narrow
# context/error helpers directly; there is no package-discovery side effect.
# These imports look "unused" to ruff but they form the package's public API.
from .static.rhinoscriptsyntax import rhinoscriptsyntax_json as rhinoscriptsyntax_json
from .server import (  # noqa: F401  (public re-exports)
    RhinoConnection,
    TOOL_REGISTRATIONS,
    get_rhino_connection,
    mcp,
    logger,
)

# Prompts
from .prompts.assert_general_strategy import (  # noqa: F401  (public re-exports)
    asset_general_strategy,
    rhinoscript_workflow,
)

# Keep package-level callable imports sourced from the explicit MCP registry.
for _tool_name, _tool_callable in TOOL_REGISTRATIONS:
    globals()[_tool_name] = _tool_callable
del _tool_name, _tool_callable
