"""Token-aware rendering of introspection results (object / enum / search).

Every hint printed back to the model names the calling product's own tool
(``idx.tool``), so a hint copied out of an RSTAB answer is a valid RSTAB call.
"""

import os

from dlubal_mcp.core.introspection.index import (
    access_path,
    field_type,
    relation_of,
    wrapper_row_type,
)

_MAX_FIELDS = 40
_MAX_ENUM = 40
_ENUM_LIST_NOFILTER = 15
_MAX_SEARCH = 25

# An object answer is capped as a whole, not per section: prefix grouping alone
# does not fit a large object into a readable answer. Measured on the installed
# API, LoadCase (187 fields) groups into 16 families plus 68 loose fields and
# CrossSection (683) into 67 plus 121 — both far past any readable budget.
_MAX_OBJECT_LINES = 60
# Fields shown per inline expansion of a nested table row.
_MAX_NESTED_FIELDS = 12
# Split of the overview budget between loose fields and the prefix-family list.
_UNGROUPED_MAX = 25
_FAMILIES_FLOOR = 8
# Prefix grouping: longest prefix first, and a family must earn its line. A
# family of 4 saves 3 lines; one of 3 saves 2 and costs a real field its place —
# at _GROUP_MIN=3, Material's identity field material_type disappears into a
# three-member "material_*" family.
_GROUP_DEPTH = 3
_GROUP_MIN = 4
# Shortest value-name word used as an enum filter example ("q", "b" narrow
# nothing a reader could recognise).
_MIN_EXAMPLE_TERM = 3


# Query terms quoted back in an answer. The echo is there to say what was
# looked for, not to repeat it: a 20 000-character query used to come back as a
# 24 000-character answer that was almost entirely its own echo.
_ECHO_TERMS = 8
_ECHO_TERM_CHARS = 40


def clip(text: str, width: int = _ECHO_TERM_CHARS) -> str:
    """``text`` shortened to ``width`` characters for quoting back."""
    return text if len(text) <= width else text[:width] + "..."


def echo_terms(terms) -> str:
    """Query terms as quoted back in an answer: de-duplicated and bounded."""
    unique = list(dict.fromkeys(terms))
    shown = str([clip(t) for t in unique[:_ECHO_TERMS]])
    hidden = len(unique) - _ECHO_TERMS
    return f"{shown[:-1]}, ... +{hidden} more terms]" if hidden > 0 else shown


def field_matches(name: str, term: str) -> bool:
    """Whether field ``name`` matches one filter ``term``.

    A term ending in ``*`` is a prefix: ``response_spectrum_*`` is exactly the
    family the grouped overview counts. Without it a family hint was a substring
    filter, and ``Member is`` (7 fields) also returned ``joist_type`` and
    ``pile_resistance`` — 47 lines for a family the overview called 7.
    """
    if term.endswith("*"):
        return name.startswith(term[:-1])
    return term in name


def _window(items, offset, limit, cap):
    """Slice ``items`` for one page. ``limit`` may lower the cap, never raise it.

    An offset past the end answers with the last page rather than an empty one:
    "fields 196-195 of 195" is a dead end, and the last page is the nearest data.
    The returned start is then lower than ``offset``; ``_past_end`` says so.
    """
    size = cap if limit is None else max(1, min(limit, cap))
    start = max(0, offset)
    if start >= len(items):
        start = max(0, len(items) - size)
    return items[start:start + size], start


def _past_end(offset, start, total, what) -> list[str]:
    """Note line for a page moved back from an offset past the end, if it was."""
    if offset <= start:
        return []
    return [f"offset={offset} is past the end ({total} {what}); "
            f"showing the last page, from offset={start}."]


def _more(shown_upto, total, what=""):
    """Trailer naming the exact call that continues a truncated list."""
    return (f"  ... (+{total - shown_upto} more{what}; "
            f"call with offset={shown_upto})")


def _expansion_target(field, parent):
    """Descriptor to expand inline under ``field``, or None.

    Only types nested inside the object being rendered are expanded — the
    ``…Table`` / ``…Row`` pattern that belongs to this object alone. A shared
    type (``common.Value``, ``ObjectId``) is a lookup of its own and gets a
    drill-down hint instead, never an inline copy under every field that uses it.
    """
    target = field.message_type
    if target is None or target.containing_type is not parent:
        return None
    return wrapper_row_type(target) or target


def _field_line(f, idx, indent="  ", hint=True) -> str:
    """One field line: name, type, relation annotation, drill-down hint."""
    line = f"{indent}{f.name}: {field_type(f)}"

    row = wrapper_row_type(f.message_type) if f.message_type is not None else None
    if row is not None:
        line += f" (rows: {row.name})"

    rel = relation_of(f, idx.product)
    if rel:
        line += f"  (ref -> {rel})"

    if not hint:
        return line

    # Enum and message fields are both dead ends without a path to follow. Only
    # enums used to get one, which is why reaching common.Value from a field
    # typed `-> Value` took four calls in practice.
    if f.enum_type is not None:
        line += f"   # {idx.tool}('{access_path(f.enum_type.full_name)} <term>')"
    elif f.message_type is not None:
        target = row if row is not None else f.message_type
        line += f"   # {idx.tool}('{access_path(target.full_name)}')"
    return line


def _expand(target, idx, lines, budget) -> None:
    """Append ``target``'s fields indented under an already-rendered field line."""
    sub = list(target.fields)
    room = max(0, min(_MAX_NESTED_FIELDS, budget))
    if len(sub) > room:
        room = max(0, room - 1)   # the "+N more" trailer costs a line of its own
    shown = sub[:room]
    # Nested lines keep their hints. Suppressing them here would just move the
    # dead end one level down — a row's `value: -> Value` or `type: enum Type`
    # is exactly the lookup the caller needs next.
    for sf in shown:
        lines.append(_field_line(sf, idx, indent="      "))
    if len(sub) > len(shown):
        lines.append(f"      ... (+{len(sub) - len(shown)} more fields of "
                     f"{target.name}; {idx.tool}('{access_path(target.full_name)}'))")


def _group_fields(fields):
    """Partition fields into (prefix, members) families plus an ungrouped rest.

    Longest prefixes first, so ``wind_simulation_wind_tunnel`` claims its fields
    before the broader ``wind_simulation`` does. Each family must reach
    ``_GROUP_MIN`` members to be worth a line of its own.

    Prefixes are case-folded because filters are: CrossSection's ``A_y`` and
    ``a_1`` are one family to the hint ``CrossSection a_*``, so they must be one
    family in the overview too, or its count disagrees with what the hint returns.
    """
    names = [f.name for f in fields]
    by_name = {f.name: f for f in fields}
    claimed: set = set()
    groups: list[tuple[str, list]] = []

    for depth in range(_GROUP_DEPTH, 0, -1):
        counts: dict[str, int] = {}
        for n in names:
            if n in claimed:
                continue
            parts = n.lower().split("_")
            if len(parts) > depth:
                prefix = "_".join(parts[:depth])
                counts[prefix] = counts.get(prefix, 0) + 1
        for prefix, _ in sorted(counts.items(), key=lambda kv: -kv[1]):
            members = [n for n in names
                       if n not in claimed and n.lower().startswith(prefix + "_")]
            if len(members) >= _GROUP_MIN:
                groups.append((prefix, [by_name[n] for n in members]))
                claimed.update(members)

    groups.sort(key=lambda g: -len(g[1]))
    rest = [by_name[n] for n in names if n not in claimed]
    return groups, rest


def _render_grouped(desc, path, fields, idx) -> list[str]:
    """Overview of a large object: loose fields first, then prefix families.

    Loose fields come first because that is where an object's identity lives
    (``no``, ``name``, ``material_type``), while the large families are usually
    boilerplate — Material's 158 fields are mostly per-object carbon-emission
    variants. Ordering families by size alone buries the useful half.
    """
    groups, rest = _group_fields(fields)
    lines = [
        f"{desc.name}  ({path}) - {len(fields)} fields, grouped by prefix. "
        f"Add a term to filter, e.g. {idx.tool}('{desc.name} {groups[0][0] + '_*' if groups else 'name'}')."
    ]

    body = _MAX_OBJECT_LINES - 1
    if groups:
        # Never starve the family list: some objects are almost entirely
        # families (CrossSection: 683 fields), others almost entirely loose.
        rest_quota = min(len(rest), _UNGROUPED_MAX, max(0, body - _FAMILIES_FLOOR - 2))
    else:
        # Nothing grouped — the loose list is the whole answer and takes the
        # whole budget, rather than being cut to the split-mode share.
        rest_quota = min(len(rest), body - 1)

    if rest:
        shown = rest[:rest_quota]
        lines += [_field_line(f, idx) for f in shown]
        if len(rest) > len(shown):
            # These are the fields no family claimed, so their position in this
            # list is not an offset into the object. Paging means the flat list.
            lines.append(f"  ... (+{len(rest) - len(shown)} more ungrouped fields; "
                         f"add a term to filter, or page all {len(fields)} fields "
                         f"in order with limit={_MAX_FIELDS})")

    if groups:
        room = max(0, _MAX_OBJECT_LINES - len(lines) - 1)
        shown_groups = groups if len(groups) <= room else groups[:max(0, room - 1)]
        if shown_groups:
            lines.append("  prefix families:")
            width = max(len(p) for p, _ in shown_groups) + 2
            for prefix, _ in shown_groups:
                # The count is what the hint returns: every field with the
                # prefix, including those a longer family listed separately
                # (response_spectrum_* also holds response_spectrum_and_*).
                count = sum(1 for f in fields if f.name.lower().startswith(prefix + "_"))
                label = f"{prefix}_*".ljust(width)
                lines.append(f"    {label} ({count} fields)"
                             f"   # {idx.tool}('{desc.name} {prefix}_*')")
        if len(groups) > len(shown_groups):
            hidden = groups[len(shown_groups):]
            lines.append(f"    ... (+{len(hidden)} more prefix families covering "
                         f"{sum(len(m) for _, m in hidden)} fields; "
                         f"add a term to filter, or page all {len(fields)} fields "
                         f"in order with limit={_MAX_FIELDS})")
    return lines


def _field_page(desc, page, idx, head) -> tuple[list[str], int]:
    """Lines for the fields of ``page`` under the object cap, and how many fit.

    ``head`` is the number of lines already spent above the fields. Inline
    expansions consume lines too, so the cap is checked against the answer as a
    whole — an object of 7 fields whose every field is a nested table
    (BaseData) otherwise runs far past it.
    """
    lines: list[str] = []
    rendered = 0
    for f in page:
        budget = _MAX_OBJECT_LINES - head - len(lines) - 1
        if budget <= 0:
            break
        target = _expansion_target(f, desc)
        # A truncated expansion still needs the hint that continues it; a
        # complete one has already shown everything the hint would fetch.
        expanded = target is not None and budget > 1
        complete = expanded and len(target.fields) <= min(_MAX_NESTED_FIELDS, budget - 1)
        lines.append(_field_line(f, idx, hint=not complete))
        rendered += 1
        if expanded:
            _expand(target, idx, lines, _MAX_OBJECT_LINES - head - len(lines) - 1)
    return lines, rendered


def render_object(descs, filters, idx, offset=0, limit=None) -> str | None:
    """Render an object's fields, or None if ``filters`` matched none of them.

    Returning None rather than advice text leaves the caller free to answer with
    data anyway — an unmatched filter is not a reason to spend a round trip.
    """
    desc = descs[0]
    path = access_path(desc.full_name)
    fields = list(desc.fields)
    if filters:
        fields = [f for f in fields if any(field_matches(f.name.lower(), t) for t in filters)]
        if not fields:
            return None

    paging = offset > 0 or limit is not None
    if not filters and not paging and len(fields) > _MAX_FIELDS:
        # The grouped overview is the default answer for a wide object. Asking
        # for a page is asking for the flat list instead.
        lines = _render_grouped(desc, path, fields, idx)
    else:
        total = len(fields)
        page, start = _window(fields, offset, limit, _MAX_FIELDS)
        clamped = offset > start
        head = 2 if clamped else 1   # the past-the-end note and the title
        body, rendered = _field_page(desc, page, idx, head)
        if clamped:
            # The window's last page counts fields, but inline tables can stop
            # it short of the end, and a "last page" that ends in "+6 more" is
            # not one. Move forward until the end fits. Each pass starts at the
            # last `rendered` fields, and passes only repeat while fewer fit,
            # so `rendered` strictly falls and the loop ends (one always fits).
            while start + rendered < total:
                start = total - rendered
                body, rendered = _field_page(desc, fields[start:], idx, head)
        # The range is what was rendered, not what the window held: inline
        # tables use up the line budget, so 40 requested fields can show 28.
        ranged = paging or start + rendered < total
        lines = _past_end(offset, start, total, "fields") + [
            f"{desc.name}  ({path})"
            + (f" - fields {start + 1}-{start + rendered} of {total}" if ranged else "")]
        lines += body
        if start + rendered < total:
            lines.append(_more(start + rendered, total, " fields"))

    if len(descs) > 1:
        others = ", ".join(access_path(d.full_name) for d in descs[1:])
        lines.append(f"(name also matches: {others})")
    return "\n".join(lines)


def _enum_filter_example(et, values, shown, idx) -> str | None:
    """A filter call for ``et`` that works when copied verbatim, or None.

    The tip used to be ``'<Name> office'`` for every enum. A short name can
    resolve to another enum (``Type`` is ``Terrain.Type`` first), and "office"
    filters nothing in most enums. So: the short name only when it is unique,
    and a term taken from a value not yet shown, which narrows the list without
    emptying it. None when every value is already on the page.
    """
    if len(shown) >= len(values):
        return None
    low = et.name.lower()
    # Objects resolve before enums, so a name an object shares is no shortcut.
    unique = len(idx.enums.get(low, ())) == 1 and low not in idx.objects
    name = et.name if unique else access_path(et.full_name)
    lows = [v.lower() for v in values]
    # The shared prefix (ACTION_CATEGORY_) is in every value and filters nothing.
    common = os.path.commonprefix(lows)
    cut = common.rfind("_") + 1
    # Values just past the page first, wrapping round: the example then points
    # at what the reader has not seen yet.
    after = values.index(shown[-1]) + 1 if shown else 0
    seen = set(shown)
    unseen = [v for v in values[after:] + values[:after] if v not in seen]
    # A term that keeps 145 of 146 values ("national") narrows nothing a reader
    # would notice, so one keeping at most half of them wins; any narrowing
    # term is the fallback.
    fallback = None
    for v in unseen + list(shown):
        for term in v.lower()[cut:].split("_"):
            if len(term) < _MIN_EXAMPLE_TERM:
                continue
            hits = sum(term in w for w in lows)
            if 0 < hits <= len(lows) // 2:
                return f"{idx.tool}('{name} {term}')"
            if fallback is None and 0 < hits < len(lows):
                fallback = f"{idx.tool}('{name} {term}')"
    return fallback


def render_enum(enums, filters, idx, offset=0, limit=None) -> str | None:
    """Render an enum's values, or None if ``filters`` matched none of them."""
    et = enums[0]
    path = access_path(et.full_name)
    values = [v.name for v in et.values]
    total = len(values)

    if filters:
        matches = [v for v in values if all(t in v.lower() for t in filters)]
        if not matches:
            return None
        body, start = _window(matches, offset, limit, _MAX_ENUM)
        lines = _past_end(offset, start, len(matches), "matches")
        lines.append(f"{et.name} ({path}) - {len(matches)} match(es) for "
                     f"{echo_terms(filters)} (of {total} values):")
        lines += [f"  {v}" for v in body]
        if body:
            lines.append(f"Usage: <obj>.<field> = {path}.{body[0]}")
        if start + len(body) < len(matches):
            lines.append(_more(start + len(body), len(matches)))
    else:
        # Unfiltered, the default page is short — an enum of 400 values is not
        # an answer. An explicit limit overrides that, up to the hard cap.
        cap = _ENUM_LIST_NOFILTER if limit is None else _MAX_ENUM
        body, start = _window(values, offset, limit, cap)
        example = _enum_filter_example(et, values, body, idx)
        lines = _past_end(offset, start, total, "values")
        lines.append(f"{et.name} ({path}) - {total} values, showing "
                     f"{start + 1}-{start + len(body)}."
                     + (f" Add a term to filter, e.g. {example}." if example else ""))
        lines += [f"  {v}" for v in body]
        if start + len(body) < total:
            lines.append(_more(start + len(body), total))

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


def render_search(idx, tokens, min_score=1, offset=0, limit=None) -> str | None:
    """Ranked keyword matches, or None if nothing scores at least ``min_score``.

    ``min_score`` is how many of the query's terms an entry must contain. The
    default of 1 is the widest net; a caller searching speculatively raises it
    so that a query with no real vocabulary in it returns nothing rather than
    everything that happens to share a common word.
    """
    # A repeated word is one term: "the load of the load case" must not score
    # an entry twice for containing "load".
    toks = list(dict.fromkeys(t.lower() for t in tokens))
    kind_rank = {"helper": 4, "method": 3, "object": 3, "field": 2, "enum": 2, "enum_value": 1}
    scored = []
    for hay, display, kind, drill in idx.search:
        score = sum(1 for t in toks if t in hay)
        if score >= min_score:
            scored.append((score, kind_rank.get(kind, 0), display, kind, drill))

    if not scored:
        return None

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    page, start = _window(scored, offset, limit, _MAX_SEARCH)
    lines = _past_end(offset, start, len(scored), "matches")
    lines.append(f"Matches for {echo_terms(tokens)}"
                 + (f" ({start + 1}-{start + len(page)} of {len(scored)})" if start else "") + ":")
    for _, _, display, kind, drill in page:
        if kind in ("field", "object", "method", "helper"):
            hint = f"   -> {idx.tool}('{drill}')"
        elif kind == "enum_value":
            hint = f"   (enum {drill})"
        else:
            hint = ""
        lines.append(f"  [{kind}] {display}{hint}")
    if start + len(page) < len(scored):
        lines.append(_more(start + len(page), len(scored)) + " or refine your query")
    return "\n".join(lines)
