"""Resolve an inspect_<product>_api query to an object, enum, method, or search result."""

from dlubal_rfem_mcp.core.introspection.index import get_index
from dlubal_rfem_mcp.core.introspection.render import (
    render_enum,
    render_helper,
    render_method,
    render_object,
    render_search,
)


def inspect(product: str, query: str) -> str:
    """Dispatch a query string to object lookup, enum lookup, or keyword search.

    RFEM query forms:
      - "LoadCase"               -> object fields
      - "LoadCase weight"        -> object fields filtered by term(s)
      - "ActionCategory office"  -> enum values matching the filter term(s)
      - "concrete addon"         -> ranked keyword search across classes/fields/enums
    """
    idx = get_index(product)
    if idx.error:
        return idx.error

    q = (query or "").strip()
    if not q:
        return (
            f"Usage: {idx.tool}('LoadCase') | "
            f"{idx.tool}('ActionCategory office') | "
            f"{idx.tool}('concrete addon')"
        )

    tokens = q.split()
    head = tokens[0]
    headl = head.lower()
    filters = [t.lower() for t in tokens[1:]]

    # Qualified path (e.g. "rfem.loading.LoadCase.ActionCategory office") — exact, unambiguous.
    if "." in head and headl in idx.by_path:
        kind, desc = idx.by_path[headl]
        if kind == "object":
            return render_object([desc], filters, idx)
        return render_enum([desc], filters, idx)

    if headl in idx.objects:
        return render_object(idx.objects[headl], filters, idx)
    if headl in idx.enums:
        return render_enum(idx.enums[headl], filters, idx)
    if headl in idx.methods:
        return render_method(idx.methods[headl])
    if headl in idx.helpers:
        return render_helper(idx.helpers[headl])
    return render_search(idx, tokens)
