"""Tests for the inspect_<product>_api introspection tools.

These exercise the real installed Dlubal APIs via descriptor reflection. They skip
automatically per product on machines where that product's API package is absent
(e.g. CI), since `dlubal.api.<product>` ships alongside the application install.
"""

import pytest

from dlubal_rfem_mcp.core.introspection import inspect
from dlubal_rfem_mcp.core.introspection.index import get_index

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
def test_relation_annotation_is_present(product):
    _skip_unless(product)
    # Each product declares `referenced_objects` as its OWN extension at its own
    # product's extension returns an empty list instead of raising, so a shared
    # global extension would silently drop every relation. This is that guard.
    out = inspect(product, "Material")
    assert f"({product}." in out, "access path should be product-scoped"

    probe = {
        "rfem": ("Member line", "(ref -> Line)"),
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
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
