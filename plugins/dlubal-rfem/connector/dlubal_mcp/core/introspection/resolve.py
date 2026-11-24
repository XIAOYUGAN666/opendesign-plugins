"""Resolve an inspect_<product>_api query to an object, enum, method, or search result.

No query answers with advice. A term that matches nothing is not a reason to
spend a round trip telling the caller to try again — every path here degrades to
the most specific data still available: a nested type, the unfiltered object, or
a keyword search on what could be salvaged from the query.
"""

from dlubal_mcp.core.introspection.index import get_index, wrapper_row_type
from dlubal_mcp.core.introspection.render import (
    clip,
    echo_terms,
    render_enum,
    render_helper,
    render_method,
    render_object,
    render_search,
)

# Shortest term worth acting on: below this a fragment is too generic to name a
# nested type or to be worth searching a split-up query on.
_MIN_NESTED_TERM = 3


def _nested_match(descs, filters, idx):
    """Rendered nested type of one of ``descs`` named by ``filters``, or None.

    ``inspect_rfem_api('SteelJoint MembersTable')`` reads as object + field
    filter, so it used to return nothing and the caller retried with a fully
    qualified path. MembersTable is not a field of SteelJoint — it is a type
    nested in it.
    """
    candidates = [t for t in filters if len(t) >= _MIN_NESTED_TERM]
    if len(filters) > 1:
        candidates.append("".join(filters))   # "members table" -> "memberstable"
    if not candidates:
        return None

    for desc in descs:
        for nested in list(desc.nested_types) + list(desc.enum_types):
            low = nested.name.lower()
            if not any(low == c or low.startswith(c) for c in candidates):
                continue
            note = f"'{nested.name}' is nested in {desc.name}, not a field of it."
            if nested in desc.enum_types:
                out = render_enum([nested], [], idx)
            else:
                # A table wrapper holds only "rows"; the columns are on the row
                # type, so that is what the caller is actually after.
                row = wrapper_row_type(nested)
                if row is not None:
                    note = (f"'{nested.name}' is a table nested in {desc.name}; "
                            f"its columns are on {row.name}.")
                out = render_object([row or nested], [], idx)
            return f"{note}\n{out}"
    return None


def _search_fallback(idx, tokens, note=None, offset=0, limit=None) -> str:
    """Keyword search, retried on the query's parts before admitting defeat.

    A name that does not exist verbatim usually still has findable pieces:
    STEEL_JOINTS_DESIGN_RESULTS matches nothing as one token and everything
    relevant as four.
    """
    out = render_search(idx, tokens, offset=offset, limit=limit)
    if out is None:
        parts: list[str] = []
        for t in tokens:
            for piece in t.replace(".", "_").split("_"):
                if len(piece) >= _MIN_NESTED_TERM and piece.lower() not in parts:
                    parts.append(piece.lower())
        if parts and parts != [t.lower() for t in tokens]:
            # Half the parts, at least two. A name built from real API vocabulary
            # clears that bar (STEEL_JOINTS_DESIGN_RESULTS -> a field carrying
            # both "steel" and "joints"); an invented one only shares a stray
            # common word with a few hundred unrelated entries.
            out = render_search(idx, parts, min_score=max(2, (len(parts) + 1) // 2),
                                offset=offset, limit=limit)
            if out is not None:
                note = ((note + " ") if note else "") + \
                    f"No match for {echo_terms(tokens)}; searched its parts instead."
    if out is None:
        # Nothing to show. Keep the note anyway: "your path was wrong" and "your
        # keyword matched nothing" are different problems with different fixes.
        return ((note + " ") if note else "") + (
            f"No {idx.product.upper()} API match for {echo_terms(tokens)}. Try a class name "
            f"(e.g. 'LoadCase'), an enum (e.g. 'ActionCategory office'), or a "
            f"keyword (e.g. 'concrete addon')."
        )
    return f"{note}\n{out}" if note else out


def _render_object_query(descs, filters, idx, offset=0, limit=None) -> str:
    """Object lookup that always answers with fields of something."""
    out = render_object(descs, filters, idx, offset, limit)
    if out is not None:
        return out
    nested = _nested_match(descs, filters, idx)
    if nested is not None:
        return nested
    # The head named a real object; showing it whole beats sending the caller
    # away to re-ask for the same thing without the terms. An unfiltered render
    # never returns None — only the filtering branch can.
    whole = render_object(descs, [], idx, offset, limit)
    return (f"No field of {descs[0].name} matches {echo_terms(filters)} - "
            f"showing the whole object.\n{whole}")


def _render_enum_query(enums, filters, idx, offset=0, limit=None) -> str:
    """Enum lookup that always answers with values."""
    out = render_enum(enums, filters, idx, offset, limit)
    if out is not None:
        return out
    whole = render_enum(enums, [], idx, offset, limit)   # unfiltered: never None
    return f"No value of {enums[0].name} matches {echo_terms(filters)}.\n{whole}"


def inspect(product: str, query: str, offset: int = 0, limit: int | None = None) -> str:
    """Dispatch a query string to object lookup, enum lookup, or keyword search.

    Forms (shown for RFEM; identical for RSTAB and RSECTION):
      - "LoadCase"                 -> object fields
      - "LoadCase weight"          -> object fields filtered by term(s)
      - "LoadCase wind_*"          -> object fields starting with the prefix
      - "SteelJoint MembersTable"  -> a type nested in the object
      - "ActionCategory office"    -> enum values matching the filter term(s)
      - "concrete addon"           -> ranked keyword search across classes/fields/enums

    ``offset`` and ``limit`` page whichever list the answer is made of: fields,
    enum values, or ranked matches. ``limit`` can lower a section's cap but
    never raise it, so paging cannot be used to demand an unbounded answer.

    ``limit`` is normalised here so the wire form and the Python form agree:
    the tools expose it as a plain integer where 0 means "unset" (a nullable
    integer renders as an untyped field in some MCP clients), while internally
    absence is None.
    """
    limit = limit if limit and limit > 0 else None
    offset = max(0, offset)
    idx = get_index(product)
    if idx.error:
        return idx.error

    q = (query or "").strip()
    if not q:
        return (f"Usage: {idx.tool}('LoadCase') | "
                f"{idx.tool}('ActionCategory office') | "
                f"{idx.tool}('concrete addon')")

    tokens = q.split()
    head = tokens[0]
    headl = head.lower()
    filters = [t.lower() for t in tokens[1:]]

    # Qualified path (e.g. "rfem.loading.LoadCase.ActionCategory office") — exact.
    if "." in head:
        entry = idx.by_path.get(headl)
        if entry is not None:
            kind, desc = entry
            if kind == "object":
                return _render_object_query([desc], filters, idx, offset, limit)
            return _render_enum_query([desc], filters, idx, offset, limit)
        # A path that does not resolve still names what it was looking for in
        # its last segment: ...ComponentsRow.SettingsTableRow.Value -> Value.
        return _search_fallback(
            idx, [head.split(".")[-1]] + tokens[1:],
            note=f"No API path '{clip(head, 200)}'.", offset=offset, limit=limit,
        )

    if headl in idx.objects:
        return _render_object_query(idx.objects[headl], filters, idx, offset, limit)
    if headl in idx.enums:
        return _render_enum_query(idx.enums[headl], filters, idx, offset, limit)
    if headl in idx.methods:
        return render_method(idx.methods[headl])
    if headl in idx.helpers:
        return render_helper(idx.helpers[headl])
    return _search_fallback(idx, tokens, offset=offset, limit=limit)
