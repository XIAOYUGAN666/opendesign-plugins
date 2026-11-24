"""Tests for the inspect_<product>_api introspection tools.

These exercise the real installed Dlubal APIs via descriptor reflection. They skip
automatically per product on machines where that product's API package is absent
(e.g. CI), since `dlubal.api.<product>` ships alongside the application install.
"""

import importlib
import re
from types import SimpleNamespace

import pytest

from dlubal_mcp.core.introspection import inspect
from dlubal_mcp.core.introspection.index import access_path, get_index
from dlubal_mcp.core.introspection.render import _MAX_OBJECT_LINES

PRODUCTS = ("rfem",)


def _available(product: str) -> bool:
    idx = get_index(product)
    return idx.error is None and bool(idx.objects)


def requires(product: str):
    return pytest.mark.skipif(
        not _available(product),
        reason=f"dlubal.api.{product} not available: {get_index(product).error}",
    )


# Every product must at least be importable for the parameterized tests to mean
# anything; each is skipped individually below via `_skip_unless`.
def _skip_unless(product: str) -> None:
    if not _available(product):
        pytest.skip(f"dlubal.api.{product} not available")


# --------------------------------------------------------------------------
# Cross-product behaviour — the contract every inspect_<product>_api shares
# --------------------------------------------------------------------------


@pytest.mark.parametrize("product", PRODUCTS)
def test_empty_query_shows_usage_naming_own_tool(product):
    _skip_unless(product)
    out = inspect(product, "")
    # The hint must name the caller's own tool, not another product's.
    assert f"inspect_{product}_api" in out
    for other in PRODUCTS:
        if other != product:
            assert f"inspect_{other}_api" not in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_material_object_lookup_lists_fields(product):
    _skip_unless(product)
    out = inspect(product, "Material")
    assert "Material" in out
    assert "material_type" in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_object_lookup_is_filterable(product):
    _skip_unless(product)
    full = inspect(product, "Material")
    narrowed = inspect(product, "Material material_type")
    assert "material_type" in narrowed
    assert narrowed.count("\n") < full.count("\n")


@pytest.mark.parametrize("product", PRODUCTS)
def test_app_method_signature_lookup(product):
    _skip_unless(product)
    # The model must be able to discover that set_base_data is keyword-only,
    # instead of guessing positional args (the timber-add-on iteration bug).
    out = inspect(product, "set_base_data")
    assert "set_base_data" in out
    assert "base_data" in out
    assert "*" in out  # keyword-only marker present in the signature


@pytest.mark.parametrize("product", PRODUCTS)
def test_unknown_query_is_helpful_not_empty(product):
    _skip_unless(product)
    out = inspect(product, "definitely_not_a_real_thing_xyz")
    assert f"No {product.upper()} API match" in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_enum_pages_forward_without_repeating_values(product):
    _skip_unless(product)
    first = inspect(product, "MaterialType")
    assert "call with offset=" in first
    offset = int(first.rsplit("call with offset=", 1)[1].split(")")[0])
    second = inspect(product, "MaterialType", offset=offset)

    def values(out):
        return [ln.strip() for ln in out.splitlines() if ln.startswith("  MATERIAL_TYPE")]

    assert values(second), "the second page must contain values"
    assert not set(values(first)) & set(values(second)), "pages must not overlap"


@pytest.mark.parametrize("product", PRODUCTS)
def test_paging_an_object_gives_the_flat_list_not_the_overview(product):
    _skip_unless(product)
    # The grouped overview is the default answer for a wide object; asking for
    # a page is asking for the fields themselves, in declaration order.
    out = inspect(product, "Material", limit=40)
    assert "grouped by prefix" not in out
    assert "fields 1-40 of " in out
    assert "call with offset=40" in out

    second = inspect(product, "Material", offset=40, limit=40)
    assert "fields 41-" in second


@pytest.mark.parametrize("product", PRODUCTS)
def test_limit_can_narrow_a_page_but_never_widen_it(product):
    _skip_unless(product)
    narrow = inspect(product, "MaterialType", limit=3)
    assert len([ln for ln in narrow.splitlines() if ln.startswith("  MATERIAL_TYPE")]) == 3
    # A limit past the hard cap is clamped, not honoured.
    huge = inspect(product, "MaterialType", limit=10_000)
    assert len([ln for ln in huge.splitlines() if ln.startswith("  MATERIAL_TYPE")]) <= 40


@pytest.mark.parametrize("product", PRODUCTS)
def test_out_of_range_offset_does_not_crash(product):
    _skip_unless(product)
    for kwargs in ({"offset": 10**6}, {"offset": -5}, {"limit": 0}, {"limit": -1}):
        out = inspect(product, "Material", **kwargs)
        assert out and "Material" in out, kwargs


@pytest.mark.parametrize("product", PRODUCTS)
def test_unset_limit_means_the_same_thing_on_both_sides(product):
    _skip_unless(product)
    # The tools spell "unset" as 0 to keep the parameter a plain integer; the
    # Python API spells it None. They must not disagree about page size.
    def shown(**kwargs):
        out = inspect(product, "MaterialType", **kwargs)
        return [ln for ln in out.splitlines() if ln.startswith("  MATERIAL_TYPE")]

    assert shown() == shown(limit=0) == shown(limit=None) == shown(limit=-1)
    assert len(shown(limit=3)) == 3


@pytest.mark.parametrize("product", PRODUCTS)
def test_paged_answers_still_respect_the_line_cap(product):
    _skip_unless(product)
    idx = get_index(product)
    for descs in idx.objects.values():
        name = descs[0].name
        for kwargs in ({"limit": 40}, {"offset": 40, "limit": 40}):
            n = len(inspect(product, name, **kwargs).splitlines())
            assert n <= _MAX_OBJECT_LINES, f"{name} {kwargs} -> {n} lines"


@pytest.mark.parametrize("product", PRODUCTS)
def test_nested_type_resolves_without_a_qualified_path(product):
    _skip_unless(product)
    # "Parent Child" reads as object + field filter, so a nested type used to
    # come back empty and the caller retried with the fully qualified path.
    out = inspect(product, "Material TemperatureTable")
    assert "nested in Material" in out
    # A table wrapper holds only "rows"; answer with the type that has columns.
    assert "TemperatureRow  (" in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_unmatched_field_filter_shows_the_object_anyway(product):
    _skip_unless(product)
    out = inspect(product, "Material not_a_field_of_material")
    assert "showing the whole object" in out
    assert "material_type" in out, "the fallback must be the real object, not advice"


@pytest.mark.parametrize("product", PRODUCTS)
def test_unmatched_enum_filter_lists_the_enum_anyway(product):
    _skip_unless(product)
    # "list all" and "rest" were real attempts to page an enum; both returned
    # nothing but an instruction to try another term.
    out = inspect(product, "MaterialType list all")
    assert "MATERIAL_TYPE" in out.upper()


@pytest.mark.parametrize("product", PRODUCTS)
def test_unresolvable_qualified_path_searches_its_last_segment(product):
    _skip_unless(product)
    # The recorded case: a path invented one segment too deep, whose last
    # segment does name a real type.
    bogus = f"{product}.structure_core.Material.TemperatureRow.Material"
    out = inspect(product, bogus)
    assert f"No API path '{bogus}'" in out
    assert "Matches for" in out, "a bad path must still answer with data"


@pytest.mark.parametrize("product", PRODUCTS)
def test_unresolvable_path_says_so_even_when_nothing_matches(product):
    _skip_unless(product)
    # A wrong path and a keyword that matches nothing need different fixes, so
    # the answer must say which one happened.
    bogus = f"{product}.structure_core.Material.NoSuchThingAtAll"
    out = inspect(product, bogus)
    assert f"No API path '{bogus}'" in out
    assert f"No {product.upper()} API match" in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_relation_annotation_is_present(product):
    _skip_unless(product)
    # Each product declares `referenced_objects` as its OWN extension at its own
    # field number (rfem 51000, rstab 51001, rsection 51002). Reading the wrong
    # product's extension returns an empty list instead of raising, so a shared
    # global extension would silently drop every relation. This is that guard.
    out = inspect(product, "Material")
    assert f"({product}." in out, "access path should be product-scoped"

    probe = {
        "rfem": ("Member line", "(ref -> Line)"),
        "rstab": ("Member node_start", "(ref -> Node)"),
        "rsection": ("Part material", "(ref -> Material)"),
    }[product]
    out = inspect(product, probe[0])
    assert probe[1] in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_indexes_are_cached_per_product(product):
    _skip_unless(product)
    idx = get_index(product)
    assert idx is get_index(product), "index must be cached, not rebuilt"
    assert idx.product == product
    assert idx.tool == f"inspect_{product}_api"


@pytest.mark.parametrize("product", PRODUCTS)
def test_message_typed_field_carries_a_drilldown_hint(product):
    _skip_unless(product)
    # Enum fields always had a path to follow; message fields did not, so a field
    # rendered as `-> Value` was a dead end. Reaching common.Value from one took
    # four calls in a real session.
    out = inspect(product, "Material temperature")
    assert f"{product}.structure_core.Material.TemperatureRow" in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_nested_table_field_is_expanded_inline(product):
    _skip_unless(product)
    # A table-valued field carries no columns itself: Material.temperature is a
    # TemperatureTable whose only field is `rows`. Both the inline expansion and
    # the hint must hop through the wrapper to the row type.
    out = inspect(product, "Material temperature")
    assert "(rows: TemperatureRow)" in out
    # Expanding the wrapper itself would yield a single useless "rows" line.
    assert not any(ln.strip().startswith("rows:") for ln in out.splitlines())
    assert "reference_temperature" in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_shared_types_are_hinted_not_inlined(product):
    _skip_unless(product)
    # common.Vector3d is a lookup of its own. Inlining it under every field that
    # uses it would repeat it across the answer and blow the line budget.
    out = inspect(product, "Material temperature")
    assert "-> TemperatureTable" in out
    assert "common." not in out.split("temperature:")[0]


@pytest.mark.parametrize("product", PRODUCTS)
def test_object_answers_stay_within_the_line_cap(product):
    _skip_unless(product)
    # Inline expansion and prefix grouping both add lines; the cap is on the
    # answer as a whole. CrossSection (683 fields) and BaseData (7 fields, each
    # one a nested table) are the two ways this overruns.
    idx = get_index(product)
    over = []
    for descs in idx.objects.values():
        name = descs[0].name
        n = len(inspect(product, name).splitlines())
        if n > _MAX_OBJECT_LINES:
            over.append((name, n))
    assert not over, f"answers over the {_MAX_OBJECT_LINES}-line cap: {over[:5]}"


@pytest.mark.parametrize("product", PRODUCTS)
def test_large_object_is_grouped_by_prefix(product):
    _skip_unless(product)
    out = inspect(product, "Material")
    assert "fields, grouped by prefix" in out
    assert "prefix families:" in out
    # A family line must be actionable, not decorative.
    assert f"{idx_tool(product)}('Material " in out


_FIELD_LINE = re.compile(r"^  \w+: ")
_RANGE = re.compile(r"(\d+)-(\d+) of (\d+)")


def _returned_field_count(out: str) -> int:
    """Fields a flat object answer stands for: shown lines plus its "+N more"."""
    shown = sum(1 for ln in out.splitlines() if _FIELD_LINE.match(ln))
    more = re.search(r"\(\+(\d+) more fields; call with offset=", out)
    return shown + (int(more.group(1)) if more else 0)


@pytest.mark.parametrize("product", PRODUCTS)
def test_enum_filter_example_works_when_copied(product):
    _skip_unless(product)
    # The tip used to be "'<Name> office'" for every enum. "Type office" resolved
    # to Terrain.Type, not the steel-joint component Type it was printed under,
    # and "office" filtered nothing in almost any enum.
    idx = get_index(product)
    tip = re.compile(rf"e\.g\. {idx_tool(product)}\('(.+?)'\)\.")
    for path, (kind, et) in idx.by_path.items():
        if kind != "enum":
            continue
        m = tip.search(inspect(product, path).splitlines()[0])
        if m is None:
            continue
        out = inspect(product, m.group(1))
        first = out.splitlines()[0]
        assert "match(es) for" in first, (path, m.group(1), first)
        assert f"({path} " in first.lower() or f"({path})" in first.lower(), (path, first)
        # It must narrow the list: 'CombinationWizardStandard national' kept
        # 145 of 146 values.
        hits, total = map(int, re.search(r"(\d+) match\(es\).*\(of (\d+) values\)", first).groups())
        assert hits < total, (path, m.group(1), hits, total)


@pytest.mark.parametrize("product", PRODUCTS)
def test_enum_shown_whole_has_no_filter_tip(product):
    _skip_unless(product)
    idx = get_index(product)
    small = next(e for es in idx.enums.values() for e in es if 1 < len(e.values) <= 15)
    out = inspect(product, access_path(small.full_name))
    assert "Add a term to filter" not in out


@pytest.mark.parametrize("product", PRODUCTS)
def test_offset_past_the_end_answers_with_the_last_page(product):
    _skip_unless(product)
    # "fields 196-195 of 195" was an empty page with no way on. Every list kind
    # must answer with its last page instead, and say why.
    for query in ("Material", "MaterialType", "material"):
        out = inspect(product, query, offset=10**6)
        assert "is past the end" in out, query
        for a, b, total in _RANGE.findall(out):
            assert int(a) <= int(b) <= int(total), (query, a, b, total)
        assert len(out.splitlines()) > 2, query


@pytest.mark.parametrize("product", PRODUCTS)
def test_page_range_is_what_the_page_shows(product):
    _skip_unless(product)
    # Inline tables use up the line budget, so a page of 40 can show 28 fields.
    # The header said "fields 121-160 of 243" for RSTAB Member anyway.
    idx = get_index(product)
    wrong = []
    for descs in idx.objects.values():
        name = descs[0].name
        offset = 0
        while True:
            out = inspect(product, name, offset=offset, limit=40)
            a, b, _ = map(int, _RANGE.search(out).groups())
            shown = sum(1 for ln in out.splitlines() if _FIELD_LINE.match(ln))
            if (a, b) != (offset + 1, offset + shown):
                wrong.append((name, offset, (a, b), shown))
                break
            nxt = re.search(r"call with offset=(\d+)\)", out)
            if nxt is None or offset >= 120:   # the first pages carry the risk
                break
            offset = int(nxt.group(1))
    assert not wrong, wrong[:5]


@pytest.mark.parametrize("product", PRODUCTS)
def test_past_the_end_page_is_really_the_last(product):
    _skip_unless(product)
    # The "last page" for RSTAB Member ended in "(+6 more fields; call with
    # offset=237)": it was sized in fields, but inline tables cut it short.
    idx = get_index(product)
    wrong = []
    for descs in idx.objects.values():
        name = descs[0].name
        out = inspect(product, name, offset=10**6)
        m = _RANGE.search(out)
        if "call with offset=" in out or (m and m.group(2) != m.group(3)):
            wrong.append(name)
    assert not wrong, wrong[:5]


@pytest.mark.parametrize("product", PRODUCTS)
def test_prefix_family_counts_match_what_their_hints_return(product):
    _skip_unless(product)
    # A family hint used to be a substring filter, so "Member is" (7 fields)
    # returned 47 lines and "LoadCase response_spectrum" (8) returned 22.
    idx = get_index(product)
    family = re.compile(rf"\((\d+) fields\)   # {idx_tool(product)}\('(.+?)'\)")
    wrong = []
    for descs in idx.objects.values():
        out = inspect(product, descs[0].name)
        if "grouped by prefix" not in out:
            continue
        for count, hint in family.findall(out):
            got = _returned_field_count(inspect(product, hint))
            if got != int(count):
                wrong.append((hint, int(count), got))
    assert not wrong, wrong[:5]


@pytest.mark.parametrize("product", PRODUCTS)
def test_long_query_echo_is_bounded(product):
    _skip_unless(product)
    # A 20 000-character query came back as a 24 000-character answer that was
    # almost all its own echo.
    blob = "x" * 20_000
    assert len(inspect(product, f"Material {blob}")) < 6_000
    assert len(inspect(product, blob)) < 1_000
    assert len(inspect(product, f"{product}.{blob}")) < 1_000
    sentence = " ".join(f"word{i}" for i in range(200))
    assert "more terms" in inspect(product, sentence)


def idx_tool(product: str) -> str:
    return f"inspect_{product}_api"


@pytest.mark.asyncio
@pytest.mark.parametrize("product", PRODUCTS)
async def test_tool_exposes_the_parameter_its_trailers_name(product, tmp_path):
    """The rendered "call with offset=N" must name a parameter that exists.

    Renaming the tool argument without the trailer (or the reverse) turns every
    truncated answer into an instruction the caller cannot follow.
    """
    from fastmcp import FastMCP

    mcp = FastMCP("test")
    module = importlib.import_module(f"dlubal_mcp.{product}.tools")
    session_cls = getattr(
        importlib.import_module(f"dlubal_mcp.{product}.session"),
        f"{product.upper()}Session",
    )
    config = SimpleNamespace(
        execution_state_file=str(tmp_path / f"{product}-executions.sqlite3"),
        url="127.0.0.1",
        api_port=9000,
    )
    module.register_tools(mcp, session_cls("", "", "127.0.0.1", 9000), config)

    tool = await mcp.get_tool(f"inspect_{product}_api")
    props = tool.parameters["properties"]
    assert "offset" in props and "limit" in props
    assert props["offset"]["default"] == 0
    # limit is clamped in the schema itself, so an over-wide page is rejected
    # before it can reach the renderer.
    assert props["limit"]["maximum"] == 40

    # Both must carry a top-level scalar type. `int | None` compiles to an
    # anyOf with no type at the top, which some MCP clients (Postman) render as
    # an untyped field and then submit as a string, failing validation. 0 is
    # the "unset" value instead, so the parameter stays a plain integer.
    for name in ("offset", "limit"):
        assert props[name].get("type") == "integer", f"{name}: {props[name]}"
        assert "anyOf" not in props[name], f"{name} must not be a nullable union"




# --------------------------------------------------------------------------
# RFEM
# --------------------------------------------------------------------------

pytestmark_rfem = requires("rfem")


@requires("rfem")
def test_rfem_object_lookup_lists_fields():
    out = inspect("rfem", "LoadCase")
    assert "LoadCase" in out
    assert "action_category" in out


@requires("rfem")
def test_rfem_enum_filter_returns_exact_value_not_all():
    out = inspect("rfem", "ActionCategory office")
    assert "OFFICE" in out.upper()
    # must not dump the whole enum
    assert "of " in out  # "... (of N values)" / "(of N)" annotation present


@requires("rfem")
def test_rfem_steel_joint_nested_tables_open_in_one_call():
    # Replay of a real 52-call session. Opening SteelJoint's tables took five
    # calls (SteelJoint -> "SteelJoint MembersTable" -> "MembersTable steel
    # joint" -> "MembersRow steel joint" -> the qualified path), and reaching
    # SettingsTableRow.value took nine more. All of it is now one answer.
    out = inspect("rfem", "SteelJoint")

    assert "(rows: MembersRow)" in out
    assert "end_type" in out and "members_no" in out
    assert "rfem.steel_joints_objects.SteelJoint.MembersRow.EndType" in out

    assert "(rows: ComponentsRow)" in out
    assert "rfem.steel_joints_objects.SteelJoint.ComponentsRow.Type" in out
    # The row's own table field points straight at its row type, not the wrapper.
    assert "rfem.steel_joints_objects.SteelJoint.ComponentsRow.SettingsTableRow" in out


@requires("rfem")
def test_rfem_value_typed_field_names_common_value():
    # `errors: -> Value` gave no path; the session spent four calls discovering
    # that Value means common.Value.
    out = inspect("rfem", "rfem.steel_joints_objects.SteelJoint.ComponentsRow")
    assert "value: -> Value" in out
    assert "inspect_rfem_api('common.Value')" in out


@requires("rfem")
def test_rfem_load_case_overview_is_grouped_not_truncated():
    out = inspect("rfem", "LoadCase")
    # 187 fields in 2.14, 195 in 2.15: the old answer showed the first 40 and
    # told the model to guess a filter term. The overview now names the
    # families it can filter by.
    assert re.search(r"\d{3} fields, grouped by prefix", out)
    assert "response_spectrum" in out
    assert "wind_simulation" in out
    # Identity and the mass-source field families stay reachable.
    assert "analysis_type" in out
    assert "import" in out


@requires("rfem")
def test_rfem_recorded_dead_end_queries_all_return_data():
    # Every inspect_rfem_api call in one recorded 52-call session that came back
    # with no usable content. Nine of the eleven were the caller reaching for a
    # nested type, paging an enum, or mistyping a path — all answerable.
    advice_only = (
        "SteelJoint MembersTable",
        "MembersRow steel joint",
        "STEEL_JOINTS_DESIGN_RESULTS",
        "Standards base_data",
        "rfem.BaseData.Standards.CombinationWizardStandardGroup AISC",
        "CombinationWizardStandardGroup list all",
        "CombinationWizardStandardGroup rest",
        "LoadCombination ItemsTable",
        "SteelJoint ComponentsTable",
        "rfem.steel_joints_objects.SteelJoint.ComponentsRow.SettingsTable.Value",
        "rfem.steel_joints_objects.SteelJoint.ComponentsRow.SettingsTableRow.Value",
    )
    for query in advice_only:
        out = inspect("rfem", query)
        assert "No RFEM API match" not in out, query
        # Data means fields, enum values, or ranked matches — not a suggestion
        # to rephrase. Every one of these answers is at least a few lines long.
        assert len(out.splitlines()) > 3, query


@requires("rfem")
def test_rfem_nonexistent_name_searches_its_parts():
    # A result-table name the model invented. It matches nothing whole and
    # everything relevant once split on underscores.
    out = inspect("rfem", "STEEL_JOINTS_DESIGN_RESULTS")
    assert "searched its parts instead" in out
    assert "steel" in out.lower() and "joint" in out.lower()


@requires("rfem")
def test_rfem_invented_name_is_not_answered_with_noise():
    # The part-split fallback must not fire for a query with no API vocabulary
    # in it: splitting finds a few hundred entries sharing one common word.
    out = inspect("rfem", "definitely_not_a_real_thing_xyz")
    assert "No RFEM API match" in out
    assert "searched its parts instead" not in out


@requires("rfem")
def test_rfem_stress_addon_trap_resolves_to_correct_field():
    # The exact bug: "stress analysis" must map to stress_strain_analysis_active,
    # never the non-existent stress_analysis_active.
    out = inspect("rfem", "stress")
    assert "stress_strain_analysis_active" in out


@requires("rfem")
def test_rfem_concrete_addon_keyword_search():
    out = inspect("rfem", "concrete addon")
    assert "concrete_design_active" in out


@requires("rfem")
def test_rfem_qualified_enum_path_lookup():
    out = inspect("rfem", "rfem.loading.LoadCase.ActionCategory office")
    assert "rfem.loading.LoadCase.ActionCategory" in out
    assert "OFFICE" in out.upper()


@requires("rfem")
def test_rfem_delete_object_signature_is_obj_not_kwargs():
    out = inspect("rfem", "delete_object")
    assert "delete_object(obj" in out  # takes a typed stub, not object_type=/no=


@requires("rfem")
def test_rfem_common_shared_types_are_indexed():
    # rfem fields reference dlubal.api.common types (Vector3d,
    # CoordinateSystemRepresentation); they must be discoverable so the model can
    # drill into nested enums like COORDINATE_SYSTEM_TYPE_LOCAL.
    out = inspect("rfem", "CoordinateSystemRepresentation")
    assert "common.CoordinateSystemRepresentation" in out
    assert "CoordinateSystemType" in out
    assert "common.Vector3d" in inspect("rfem", "Vector3d")


@requires("rfem")
def test_rfem_coordinate_system_local_enum_value():
    out = inspect("rfem", "CoordinateSystemType local")
    assert "COORDINATE_SYSTEM_TYPE_LOCAL" in out


@requires("rfem")
def test_rfem_tree_table_helper_functions_are_indexed():
    # Helper functions for reading tree tables (get_design_settings results) are plain
    # functions in common.tree_table — must be discoverable with their signatures.
    out = inspect("rfem", "get_value_by_path")
    assert "common.tree_table.get_value_by_path" in out
    assert "path" in out


@requires("rfem")
def test_rfem_tree_table_helpers_findable_by_topic():
    out = inspect("rfem", "tree table")
    assert "get_value_by_path" in out or "get_values_by_key" in out


# --------------------------------------------------------------------------
# RSTAB — locks in the facts its tool description now asserts
# --------------------------------------------------------------------------








# --------------------------------------------------------------------------
# RSECTION — locks in the facts its tool description now asserts
# --------------------------------------------------------------------------
