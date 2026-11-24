"""The cross-section guard against a fake RFEM that behaves as RFEM 6.16 did live.

Observed 2026-11-06 (RFEM 6.16.0005, dlubal.api 2.16.5): a rejected name leaves
an empty TYPE_BASIC section with area 0, a list stops at the first rejection, a
failed update and a create on a taken number change nothing, a parametric name
gains "/H", and manufacturing_type on a catalog name is ignored.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dlubal_mcp.rfem import section_guard
from dlubal_mcp.rfem.execution import ExecutionManager
from dlubal_mcp.rfem.model_binding import BoundApplication, bound_connection

TYPES = {
    0: "TYPE_UNKNOWN",
    1: "TYPE_STANDARDIZED_STEEL",
    3: "TYPE_PARAMETRIC_THIN_WALLED",
    11: "TYPE_BASIC",
}
MANUFACTURING = {
    0: "MANUFACTURING_TYPE_UNKNOWN",
    2: "MANUFACTURING_TYPE_HOT_ROLLED",
    3: "MANUFACTURING_TYPE_COLD_FORMED",
}


def _enum(names):
    values = {n: SimpleNamespace(name=v) for n, v in names.items()}
    return SimpleNamespace(enum_type=SimpleNamespace(values_by_number=values))


class CrossSection:
    DESCRIPTOR = SimpleNamespace(
        full_name="dlubal.api.rfem.structure_core.CrossSection",
        fields_by_name={
            "type": _enum(TYPES),
            "manufacturing_type": _enum(MANUFACTURING),
        },
    )

    def __init__(
        self,
        no=0,
        name=None,
        type=None,
        manufacturing_type=None,
        area_axial=0.0,
        material=None,
    ):
        self.no = no
        self._set = {
            k
            for k, v in (
                ("name", name),
                ("type", type),
                ("manufacturing_type", manufacturing_type),
            )
            if v is not None
        }
        self.name = name or ""
        self.type = type or 0
        self.manufacturing_type = manufacturing_type or 0
        self.area_axial = area_axial

    def HasField(self, field):
        return field in self._set


class Node:
    DESCRIPTOR = SimpleNamespace(full_name="dlubal.api.rfem.structure_core.Node")

    def __init__(self, no=0):
        self.no = no


CATALOG = {"CHS 457x20": 0.02746, "IPE 200": 0.00285, "HEB 300": 0.0149}


class Rfem:
    """Stores cross-sections the way RFEM 6.16 did in the live probe."""

    rejection = (
        "Failed to create Cross-Section No. {no}.  Invalid cross-section name '{name}'."
    )

    def __init__(self):
        self.guid = str(uuid4())
        self.sections = {}
        self.calls = []

    def get_model_list(self):
        return SimpleNamespace(model_info=[SimpleNamespace(guid=self.guid)])

    def get_object_id_list(self, object_type=None, model_id=None):
        self.calls.append("ids")
        return SimpleNamespace(
            object_id=[SimpleNamespace(no=n) for n in sorted(self.sections)]
        )

    def get_object(self, obj, model_id=None):
        self.calls.append(("get", obj.no))
        if obj.no not in self.sections:
            raise RuntimeError(f"Cross-Section No. {obj.no} not found")
        return self.sections[obj.no]

    def delete_object(self, obj, model_id=None):
        self.calls.append(("delete", obj.no))
        del self.sections[obj.no]

    def create_object(self, obj, model_id=None):
        self.calls.append(("create", obj.no))
        if isinstance(obj, Node):
            return
        if obj.no in self.sections:
            raise RuntimeError(
                f"Failed to create Cross-Section No. {obj.no}.  Cross-Section No. {obj.no} already exists."
            )
        stored = self._resolve(obj.name)
        if stored is None:
            self.sections[obj.no] = CrossSection(obj.no, obj.name, type=11)
            raise RuntimeError(self.rejection.format(no=obj.no, name=obj.name))
        self.sections[obj.no] = CrossSection(obj.no, *stored)

    def create_object_list(self, objs, model_id=None):
        for obj in objs:
            self.create_object(obj)

    def update_object(self, obj, model_id=None):
        self.calls.append(("update", obj.no))
        stored = self._resolve(obj.name)
        if stored is None:
            raise RuntimeError(
                f"Failed to update Cross-Section No. {obj.no}.  Invalid cross-section name '{obj.name}'."
            )
        self.sections[obj.no] = CrossSection(obj.no, *stored)

    def _resolve(self, name):
        if name in CATALOG:
            return name, 1, 2, CATALOG[name]  # manufacturing_type requests are ignored
        if name == "CHS 456/20":
            return "CHS 456/20/H", 3, 2, 0.02739
        if name.startswith("CHS 456/") and name.count("/") == 1:
            return f"{name}/H", 3, 2, 0.02739
        if name.replace(" ", "").lower() == "w10x15":
            return "W 10x15", 1, 2, 0.00285  # catalog spelling of the same section
        return None


COMMON = SimpleNamespace(ModelId=SimpleNamespace, OptionalModelId=SimpleNamespace)


@pytest.fixture(autouse=True)
def _object_type(monkeypatch):
    monkeypatch.setattr(
        section_guard, "_cross_section_type", lambda section: "CROSS_SECTION"
    )


def _bound(rfem):
    lines = []
    return BoundApplication(
        rfem, COMMON, rfem.guid, lambda _: None, lines.append
    ), lines


def test_rejected_name_leaves_no_section_and_keeps_the_original_error():
    rfem = Rfem()
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError, match="Invalid cross-section name") as rejected:
        bound.create_object(CrossSection(3, "CHS 456x20", material=1))
    assert rfem.sections == {}
    assert "No. 3 'CHS 456x20' was rejected by RFEM" in lines[0]
    assert "removed; the model holds no trace" in lines[0]
    assert "ask the user" in lines[-1]
    # The write latch still stops a quiet substitute in the same script.
    with pytest.raises(RuntimeError) as blocked:
        bound.create_object(CrossSection(3, "CHS 457x20"))
    assert blocked.value is rejected.value
    assert ("create", 3) in rfem.calls and rfem.calls.count(("create", 3)) == 1


def test_accepted_name_is_reported_as_stored():
    rfem = Rfem()
    bound, lines = _bound(rfem)
    bound.create_object(CrossSection(1, "CHS 457x20", material=1))
    assert lines == [
        (
            "[cross-section] No. 1: requested 'CHS 457x20' -> stored 'CHS 457x20' "
            "(TYPE_STANDARDIZED_STEEL, MANUFACTURING_TYPE_HOT_ROLLED)."
        )
    ]


def test_parametric_name_changed_by_rfem_is_flagged():
    rfem = Rfem()
    bound, lines = _bound(rfem)
    bound.create_object(CrossSection(10, "CHS 456/20"))
    assert "stored 'CHS 456/20/H'" in lines[0]
    assert "DIFFERS from the request in name" in lines[0]


def test_ignored_manufacturing_type_is_flagged():
    rfem = Rfem()
    bound, lines = _bound(rfem)
    bound.create_object(CrossSection(11, "CHS 457x20", manufacturing_type=3))
    assert "DIFFERS from the request in manufacturing_type" in lines[0]
    assert "confirm it with the user" in lines[0]


def test_create_on_a_taken_number_deletes_nothing():
    rfem = Rfem()
    rfem.sections[1] = CrossSection(
        1, "CHS 457x20", type=1, manufacturing_type=2, area_axial=0.02746
    )
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError, match="already exists"):
        bound.create_object(CrossSection(1, "XYZ 123"))
    assert rfem.sections[1].name == "CHS 457x20"
    assert not any(
        call[0] == "delete" for call in rfem.calls if isinstance(call, tuple)
    )
    assert lines == []


def test_list_reports_created_removed_and_skipped_sections():
    rfem = Rfem()
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError, match="No. 7"):
        bound.create_object_list(
            [
                CrossSection(6, "IPE 200"),
                CrossSection(7, "XYZ 123"),
                CrossSection(8, "HEB 300"),
            ]
        )
    assert sorted(rfem.sections) == [6]
    text = "\n".join(lines)
    assert "No. 7 'XYZ 123' was rejected" in text
    assert "No. 6: requested 'IPE 200' -> stored 'IPE 200'" in text
    assert "Not attempted after the rejection: Nos. 8." in text


def test_failed_update_changes_nothing_and_cleans_nothing():
    rfem = Rfem()
    rfem.sections[1] = CrossSection(
        1, "CHS 457x20", type=1, manufacturing_type=2, area_axial=0.02746
    )
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError, match="Failed to update"):
        bound.update_object(CrossSection(1, "XYZ 123"))
    assert rfem.sections[1].name == "CHS 457x20"
    assert lines == []


def test_successful_rename_is_read_back():
    rfem = Rfem()
    rfem.sections[1] = CrossSection(
        1, "IPE 200", type=1, manufacturing_type=2, area_axial=0.00285
    )
    bound, lines = _bound(rfem)
    bound.update_object(CrossSection(1, "HEB 300"))
    assert "No. 1: requested 'HEB 300' -> stored 'HEB 300'" in lines[0]


def test_unexpected_object_under_the_rejected_number_is_kept():
    rfem = Rfem()
    original = rfem.create_object

    def create_then_replace(obj, model_id=None):
        try:
            original(obj)
        finally:
            rfem.sections[obj.no] = CrossSection(
                obj.no, "IPE 200", type=1, area_axial=0.00285
            )

    rfem.create_object = create_then_replace
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError):
        bound.create_object(CrossSection(5, "XYZ 123"))
    assert rfem.sections[5].name == "IPE 200"
    assert "which is not RFEM's empty leftover, so it was kept" in lines[0]


def test_unreadable_model_before_the_write_cleans_nothing():
    rfem = Rfem()

    def no_ids(object_type=None, model_id=None):
        raise RuntimeError("ids unavailable")

    rfem.get_object_id_list = no_ids
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError, match="Invalid"):
        bound.create_object(CrossSection(5, "XYZ 123"))
    assert 5 in rfem.sections
    assert "nothing was cleaned up" in lines[0]


def test_failed_cleanup_still_raises_the_rejection():
    rfem = Rfem()

    def no_delete(obj, model_id=None):
        raise RuntimeError("delete failed")

    rfem.delete_object = no_delete
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError, match="Invalid cross-section name"):
        bound.create_object(CrossSection(5, "XYZ 123"))
    assert "could not be removed: delete No. 5" in lines[0]


def test_other_objects_and_generators_pass_through_untouched():
    rfem = Rfem()
    bound, lines = _bound(rfem)
    bound.create_object(Node(1))
    sections = (s for s in [CrossSection(1, "IPE 200")])
    bound.create_object_list(sections)
    assert rfem.calls == [("create", 1), ("create", 1)]
    assert lines == []


def test_script_output_carries_the_guard_report(tmp_path):
    rfem = Rfem()

    class Session:
        @contextmanager
        def connection(self):
            yield rfem

    def work(capture, record):
        with bound_connection(
            Session(), COMMON, rfem.guid, record, capture.print
        ) as bound:
            try:
                bound.create_object(CrossSection(3, "CHS 456x20"))
            except RuntimeError:
                capture.print("caught")

    manager = ExecutionManager(tmp_path / "state.db")
    result = manager.submit(str(uuid4()), "chat", "code", work, wait_seconds=5)
    assert result["state"] == "failed"
    assert "Invalid cross-section name" in result["error"]
    assert "[cross-section] No. 3 'CHS 456x20' was rejected by RFEM" in result["output"]
    assert rfem.sections == {}


def test_leftover_is_found_when_the_error_text_names_no_number():
    rfem = Rfem()
    rfem.rejection = "Querschnitt konnte nicht angelegt werden: '{name}'."
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError, match="Querschnitt"):
        bound.create_object_list(
            [CrossSection(6, "IPE 200"), CrossSection(7, "XYZ 123")]
        )
    assert sorted(rfem.sections) == [6]
    assert "No. 7 'XYZ 123' was rejected by RFEM" in "\n".join(lines)


def test_failure_without_a_leftover_cleans_nothing_and_says_nothing():
    rfem = Rfem()

    def unavailable(obj, model_id=None):
        raise RuntimeError("StatusCode.UNAVAILABLE: failed to connect")

    rfem.create_object = unavailable
    bound, lines = _bound(rfem)
    with pytest.raises(RuntimeError, match="UNAVAILABLE"):
        bound.create_object(CrossSection(3, "IPE 200"))
    assert lines == []
    assert not [c for c in rfem.calls if isinstance(c, tuple) and c[0] == "delete"]


def test_spelling_only_difference_is_not_a_question_for_the_user():
    rfem = Rfem()
    bound, lines = _bound(rfem)
    bound.create_object(CrossSection(1, "w10x15"))
    assert "stored 'W 10x15'" in lines[0]
    assert "RFEM's spelling of the same name" in lines[0]
    assert "DIFFERS" not in lines[0]


def test_output_budget_keeps_changed_sections_and_stays_small():
    rfem = Rfem()
    bound, lines = _bound(rfem)
    for no in range(1, 61):
        bound.create_object(CrossSection(no, "IPE 200"))
    for no in range(61, 181):
        bound.create_object(CrossSection(no, f"CHS 456/{no}"))
    assert len(lines) == section_guard.FULL_LINES + 1 + 99 + 1
    assert lines[section_guard.FULL_LINES] == section_guard.FULL_LINES_USED
    assert all("DIFFERS in name" in line for line in lines[51:150])
    assert lines[-1] == section_guard.ALL_LINES_USED
    assert len("\n".join(lines)) < 25_000
    # Every section was still read back; only the listing is bounded.
    assert sum(1 for c in rfem.calls if c[0] == "get") >= 180
