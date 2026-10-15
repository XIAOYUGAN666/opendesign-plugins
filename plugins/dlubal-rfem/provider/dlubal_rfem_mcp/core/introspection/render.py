"""Token-aware rendering of introspection results (object / enum / search).

Every hint printed back to the model names the calling product's own tool
(``idx.tool``), so a hint copied out of an RFEM answer is a valid RFEM call.
"""

from dlubal_rfem_mcp.core.introspection.index import (
    access_path,
    field_type,
    relation_of,
)

_MAX_FIELDS = 40
_MAX_ENUM = 40
_ENUM_LIST_NOFILTER = 15
_MAX_SEARCH = 25


def render_object(descs, filters, idx) -> str:
    desc = descs[0]
    path = access_path(desc.full_name)
    fields = list(desc.fields)
    if filters:
        fields = [f for f in fields if any(t in f.name.lower() for t in filters)]
        if not fields:
            return (
                f"{desc.name} ({path}): no field matches {filters}. "
                f"Run {idx.tool}('{desc.name}') to list all fields."
            )

    total = len(fields)
    shown = fields[:_MAX_FIELDS]
    lines = [f"{desc.name}  ({path})"]
    for f in shown:
        line = f"  {f.name}: {field_type(f)}"
        rel = relation_of(f, idx.product)
        if rel:
            line += f"  (ref -> {rel})"
        if f.enum_type is not None:
            line += f"   # {idx.tool}('{access_path(f.enum_type.full_name)} <term>')"
        lines.append(line)
    if total > len(shown):
        lines.append(
            f"  ... (+{total - len(shown)} more fields; add a term to filter, "
            f"e.g. {idx.tool}('{desc.name} <term>'))"
        )
    if len(descs) > 1:
        others = ", ".join(access_path(d.full_name) for d in descs[1:])
        lines.append(f"(name also matches: {others})")
    return "\n".join(lines)


def render_enum(enums, filters, idx) -> str:
    et = enums[0]
    path = access_path(et.full_name)
    values = [v.name for v in et.values]
    total = len(values)

    if filters:
        matches = [v for v in values if all(t in v.lower() for t in filters)]
        if not matches:
            return (
                f"{et.name}: no value matches {filters} (of {total}). "
                f"Try another term. Path: {path}"
            )
        lines = [
            f"{et.name} ({path}) - {len(matches)} match(es) for {filters} (of {total} values):"
        ]
        body = matches[:_MAX_ENUM]
        lines += [f"  {v}" for v in body]
        lines.append(f"Usage: <obj>.<field> = {path}.{body[0]}")
        if len(matches) > len(body):
            lines.append(f"  ... (+{len(matches) - len(body)} more; refine the term)")
    else:
        head = min(_ENUM_LIST_NOFILTER, total)
        lines = [
            (
                f"{et.name} ({path}) - {total} values. Add a term to filter, e.g. "
                f"{idx.tool}('{et.name} office'). First {head}:"
            )
        ]
        lines += [f"  {v}" for v in values[:head]]

    if len(enums) > 1:
        others = ", ".join(access_path(e.full_name) for e in enums[1:])
        lines.append(f"(name also matches: {others})")
    return "\n".join(lines)


def render_method(method) -> str:
    name, sig, summary = method
    lines = [f"app.{name}{sig}"]
    if summary:
        lines.append(f"  {summary}")
    return "\n".join(lines)


def render_helper(helper) -> str:
    name, sig, summary = helper
    lines = [f"{name}{sig}"]
    if summary:
        lines.append(f"  {summary}")
    return "\n".join(lines)


def render_search(idx, tokens) -> str:
    toks = [t.lower() for t in tokens]
    kind_rank = {
        "helper": 4,
        "method": 3,
        "object": 3,
        "field": 2,
        "enum": 2,
        "enum_value": 1,
    }
    scored = []
    for hay, display, kind, drill in idx.search:
        score = sum(1 for t in toks if t in hay)
        if score:
            scored.append((score, kind_rank.get(kind, 0), display, kind, drill))

    if not scored:
        return (
            f"No {idx.product.upper()} API match for {tokens}. Try a class name (e.g. 'LoadCase'), "
            f"an enum (e.g. 'ActionCategory office'), or a keyword (e.g. 'concrete addon')."
        )

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    lines = [f"Matches for {tokens}:"]
    for _, _, display, kind, drill in scored[:_MAX_SEARCH]:
        if kind in ("field", "object", "method", "helper"):
            hint = f"   -> {idx.tool}('{drill}')"
        elif kind == "enum_value":
            hint = f"   (enum {drill})"
        else:
            hint = ""
        lines.append(f"  [{kind}] {display}{hint}")
    if len(scored) > _MAX_SEARCH:
        lines.append(f"  ... (+{len(scored) - _MAX_SEARCH} more; refine your query)")
    return "\n".join(lines)
