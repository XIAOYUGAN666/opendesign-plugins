"""Cross-section writes leave no trace of a rejected name and report what was stored.

Verified live on RFEM 6.16.0005 with dlubal.api 2.16.5 (2026-11-06):

- create_object with a name RFEM rejects raises, yet leaves an empty section
  under that number: TYPE_BASIC, no manufacturing type, area 0. Every later
  member that references the number gets that empty section.
- create_object_list stops at the first rejected name. Sections before it are
  created, the rejected one leaves the same empty section, the rest are skipped.
- A failed update_object, and a create on a number that already exists, leave
  the stored section untouched.
- A successful write can still differ from the request: a parametric
  "CHS 456/20" is stored as hot-rolled "CHS 456/20/H", and a manufacturing_type
  given with a catalog name is ignored.

So the empty section of a rejected create is removed, and every cross-section
write reports the stored name, type and manufacturing type, with the fields
that differ from the request. Both call the SDK directly with the bound model:
the script's failed write has already latched every further write through the
BoundApplication, and this cleanup is the guard's own, not the script's.

The leftover is found by its number in RFEM's error text and, when the text does
not name one (another UI language, a reworded message), by its shape alone. The
report has a per-execution line budget: script output is capped and keeps its
beginning, so a loop over hundreds of sections must not push the script's own
prints out of it.
"""

import importlib
import logging
import re

logger = logging.getLogger("dlubal_mcp.rfem")

# RSECTION has a CrossSection message too, but its behavior is unverified.
GUARDED_TYPES = frozenset(
    {
        "dlubal.api.rfem.structure_core.CrossSection",
        "dlubal.api.rstab.structure_core.CrossSection",
    }
)
GUARDED_WRITES = frozenset(
    {"create_object", "create_object_list", "update_object", "update_object_list"}
)
REPORTED_FIELDS = ("name", "type", "manufacturing_type")
LEFTOVER_TYPES = frozenset({"TYPE_BASIC", "TYPE_UNKNOWN"})
REJECTED_NUMBER = re.compile(r"Cross-Section No\. (\d+)")
# About 200 characters per full line and 100 per short one: at most ~20 KB of
# the 64 KB script output.
FULL_LINES = 50
SHORT_LINES = 100
PREFIX = "[cross-section]"
ASK_USER = (
    f"{PREFIX} Do not substitute another size, standard or a parametric section "
    "on your own: ask the user which section is meant. Catalog names use 'x' "
    "(e.g. CHS 457x20); parametric names use '/' and carry the manufacturing type "
    "(e.g. CHS 456/20/H hot-rolled, CHS 456/20/C cold-formed)."
)
FULL_LINES_USED = (
    f"{PREFIX} {FULL_LINES} cross-sections listed. Further ones are listed only "
    "when RFEM stored something other than requested."
)
ALL_LINES_USED = (
    f"{PREFIX} Line limit reached: no further cross-sections are listed. Read them "
    "back before reporting them to the user."
)


def guarded_write(bound, name, args, kwargs, write):
    """Run ``write`` and apply the cross-section guard when it writes sections."""
    sections = _sections(name, args, kwargs)
    if not sections:
        return write()
    creating = name.startswith("create_")
    existing = _existing_numbers(bound, sections[0]) if creating else None
    try:
        result = write()
    except Exception as exc:
        if creating:
            _after_rejection(bound, sections, existing, exc)
        raise
    if creating or any(_requested(s) for s in sections):
        _report_stored(bound, sections)
    return result


def _sections(name, args, kwargs):
    if name not in GUARDED_WRITES:
        return []
    if name.endswith("_list"):
        objs = args[0] if args else kwargs.get("objs")
    else:
        objs = [args[0] if args else kwargs.get("obj")]
    # Never iterate a generator the SDK still has to consume.
    if not isinstance(objs, (list, tuple)):
        return []
    return [obj for obj in objs if _type_name(obj) in GUARDED_TYPES]


def _type_name(obj):
    return getattr(getattr(obj, "DESCRIPTOR", None), "full_name", "")


def _cross_section_type(section):
    product = importlib.import_module(_type_name(section).split(".structure_core.")[0])
    return product.ObjectType.OBJECT_TYPE_CROSS_SECTION


def _existing_numbers(bound, section):
    try:
        ids = bound._app.get_object_id_list(
            object_type=_cross_section_type(section), model_id=bound._model()
        )
        return {item.no for item in ids.object_id}
    except Exception:
        logger.debug("Cross-section numbers unreadable before the write", exc_info=True)
        return None


def _stored(bound, section):
    return bound._app.get_object(type(section)(no=section.no), model_id=bound._model())


def _after_rejection(bound, sections, existing, exc):
    match = REJECTED_NUMBER.search(str(exc))
    number = int(match.group(1)) if match else None
    if existing is None:
        named = next((s for s in sections if s.no == number), None)
        if named is not None:
            bound._report(
                f"{PREFIX} No. {number} '{named.name}' was rejected by RFEM. The "
                "model's cross-sections could not be read beforehand, so nothing was "
                f"cleaned up: check whether No. {number} is left as an empty section."
            )
            bound._report(ASK_USER)
        return
    if number in existing:
        # "already exists" and similar: RFEM left the stored section untouched.
        return
    fresh = [s for s in sections if s.no not in existing]
    if number is None:
        rejected = _first_leftover(bound, fresh)
    else:
        rejected = next((s for s in fresh if s.no == number), None)
    if rejected is None:
        return
    position = next(i for i, s in enumerate(sections) if s is rejected)
    if position:
        _report_stored(bound, sections[:position])
    bound._report(_leftover_outcome(bound, rejected))
    skipped = [s.no for s in sections[position + 1 :]]
    if skipped:
        bound._report(
            f"{PREFIX} Not attempted after the rejection: Nos. {_numbers(skipped)}."
        )
    bound._report(ASK_USER)


def _first_leftover(bound, fresh):
    """The rejected section when the error text names no number: its empty shape."""
    for section in fresh:
        try:
            if _is_leftover(_stored(bound, section), section):
                return section
        except Exception:
            logger.debug("No cross-section stored under a fresh number", exc_info=True)
    return None


def _leftover_outcome(bound, section):
    rejected = f"{PREFIX} No. {section.no} '{section.name}' was rejected by RFEM."
    try:
        stored = _stored(bound, section)
    except Exception:
        logger.debug("No cross-section stored after the rejection", exc_info=True)
        return f"{rejected} Nothing was stored under No. {section.no}."
    if not _is_leftover(stored, section):
        return (
            f"{rejected} No. {section.no} now holds '{stored.name}', which is not "
            "RFEM's empty leftover, so it was kept: check it."
        )
    try:
        bound._app.delete_object(type(section)(no=section.no), model_id=bound._model())
    except Exception:
        logger.warning("Removing a rejected cross-section failed", exc_info=True)
        return (
            f"{rejected} The empty section RFEM left behind could not be removed: "
            f"delete No. {section.no} before members use it."
        )
    return f"{rejected} The empty section RFEM left behind was removed; the model holds no trace of it."


def _is_leftover(stored, section):
    return (
        _enum_name(stored, "type") in LEFTOVER_TYPES
        and not stored.area_axial
        and stored.name == section.name
    )


def _requested(section):
    return [f for f in REPORTED_FIELDS if section.HasField(f)]


def _same_spelling(requested, stored):
    """Same name apart from case and spaces: RFEM's spelling, not another section."""
    return requested.replace(" ", "").lower() == stored.replace(" ", "").lower()


def _report_stored(bound, sections):
    for section in sections:
        try:
            stored = _stored(bound, section)
        except Exception:
            logger.debug("Cross-section read-back failed", exc_info=True)
            _list(
                bound,
                f"{PREFIX} No. {section.no}: written, but it could not be read back.",
                True,
            )
            continue
        differs = [
            f for f in _requested(section) if getattr(section, f) != getattr(stored, f)
        ]
        spelling = "name" in differs and _same_spelling(section.name, stored.name)
        if spelling:
            differs.remove("name")
        requested = f"'{section.name}'" if section.HasField("name") else "no name"
        line = (
            f"{PREFIX} No. {section.no}: requested {requested} -> stored "
            f"'{stored.name}' ({_enum_name(stored, 'type')}, "
            f"{_enum_name(stored, 'manufacturing_type')})"
        )
        if spelling:
            line += ", RFEM's spelling of the same name"
        if differs:
            line += (
                f". DIFFERS from the request in {', '.join(differs)}: RFEM chose this, "
                "so confirm it with the user before relying on it"
            )
        short = (
            f"{PREFIX} No. {section.no}: {requested} stored as '{stored.name}' "
            f"(DIFFERS in {', '.join(differs)})."
        )
        _list(bound, line + ".", bool(differs), short)


def _list(bound, line, notable, short=None):
    """Report one section within the per-execution budget."""
    used = bound._section_lines
    if used < FULL_LINES:
        bound._report(line)
    elif used == FULL_LINES:
        bound._report(FULL_LINES_USED)
        if notable:
            bound._report(short or line)
            used += 1
    elif not notable:
        return
    elif used < FULL_LINES + SHORT_LINES:
        bound._report(short or line)
    elif used == FULL_LINES + SHORT_LINES:
        bound._report(ALL_LINES_USED)
    else:
        return
    bound._section_lines = used + 1


def _enum_name(message, field):
    value = getattr(message, field)
    try:
        return (
            message.DESCRIPTOR.fields_by_name[field]
            .enum_type.values_by_number[value]
            .name
        )
    except (AttributeError, KeyError):
        return str(value)


def _numbers(numbers):
    return ", ".join(str(n) for n in numbers)
