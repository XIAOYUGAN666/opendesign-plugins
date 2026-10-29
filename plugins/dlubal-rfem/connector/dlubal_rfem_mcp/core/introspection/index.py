"""Build a cached, searchable index of an installed Dlubal protobuf API package.

Pure reflection over ``dlubal.api.<product>`` descriptors — no connection to the
application required. The names produced here are exactly what that product's
``run_<product>_script`` tool accepts (protobuf is the source of truth, never the
pydantic mirror, whose enum names can diverge).

One index is built and cached for RFEM.
"""

import importlib
import logging

from google.protobuf.descriptor import Descriptor
from google.protobuf.descriptor import FieldDescriptor as FD

logger = logging.getLogger("dlubal_rfem_mcp.introspection")

_PREFIX = "dlubal.api."

_SCALAR = {
    FD.TYPE_DOUBLE: "float",
    FD.TYPE_FLOAT: "float",
    FD.TYPE_INT64: "int",
    FD.TYPE_UINT64: "int",
    FD.TYPE_INT32: "int",
    FD.TYPE_UINT32: "int",
    FD.TYPE_FIXED64: "int",
    FD.TYPE_FIXED32: "int",
    FD.TYPE_SFIXED32: "int",
    FD.TYPE_SFIXED64: "int",
    FD.TYPE_SINT32: "int",
    FD.TYPE_SINT64: "int",
    FD.TYPE_BOOL: "bool",
    FD.TYPE_STRING: "str",
    FD.TYPE_BYTES: "bytes",
}


def access_path(full_name: str) -> str:
    """proto full_name -> python access path used in the script tools (``<product>.<...>``)."""
    return full_name.removeprefix(_PREFIX)


def field_type(field) -> str:
    """Readable type label for a FieldDescriptor."""
    if field.enum_type is not None:
        base = f"enum {field.enum_type.name}"
    elif field.message_type is not None:
        base = f"-> {field.message_type.name}"
    else:
        base = _SCALAR.get(field.type, "scalar")
    if field.is_repeated:
        base = f"list[{base}]"
    return base


# --- referenced-object relation annotation ----------------------------------
# Many int fields are really pointers to another object (e.g. Member.line -> Line).
# The target type is carried by a custom FieldOptions extension that is baked into
# the compiled descriptor, so it is always available. The extension is REPEATED
# (`repeated ReferencedObject referenced_objects = <n>;`), so it must be read as
# a list — never via HasExtension(), which raises on repeated extensions.
#
# RFEM declares its extension at field number 51000. Resolve it from its own
# descriptors to avoid silently dropping relation annotations.
_REF_EXT: dict[str, object] = {}


def _ref_ext(product: str):
    if product not in _REF_EXT:
        try:
            rop = importlib.import_module(f"dlubal.api.{product}.referenced_object_pb2")
            _REF_EXT[product] = getattr(rop, "referenced_objects", False)
        except Exception:
            logger.debug("reference extension unavailable", exc_info=True)
            _REF_EXT[product] = False
    return _REF_EXT[product] or None


def relation_of(field, product: str):
    """Return the referenced object message name(s) for a relation field, or None."""
    ext = _ref_ext(product)
    if ext is None:
        return None
    try:
        refs = field.GetOptions().Extensions[ext]  # repeated ReferencedObject
        names = [r.object_message_name for r in refs if r.object_message_name]
        return ", ".join(names) or None
    except Exception:
        logger.debug("reference unavailable", exc_info=True)
        return None


class Index:
    """Cached maps of object/enum descriptors plus a flat search list, for one product."""

    def __init__(self, product: str) -> None:
        self.product = product
        self.tool = f"inspect_{product}_api"
        self.objects: dict[str, list] = {}  # simple name (lower) -> [Descriptor]
        self.enums: dict[str, list] = {}  # simple name (lower) -> [EnumDescriptor]
        self.by_path: dict[
            str, tuple
        ] = {}  # access_path (lower) -> ("object"|"enum", descriptor)
        self.methods: dict[
            str, tuple
        ] = {}  # method name (lower) -> (name, signature, summary)
        self.helpers: dict[
            str, tuple
        ] = {}  # helper fn/method (lower) -> (qualified_name, signature, summary)
        self.search: list[tuple] = []  # (haystack, display, kind, drilldown)
        self.error: str | None = None

    def _add_message(self, desc, seen: set) -> None:
        if desc.full_name in seen:
            return
        seen.add(desc.full_name)
        path = access_path(desc.full_name)
        self.objects.setdefault(desc.name.lower(), []).append(desc)
        self.by_path[path.lower()] = ("object", desc)
        self.search.append((desc.name.lower(), path, "object", desc.name))
        for f in desc.fields:
            self.search.append(
                (
                    f"{desc.name}.{f.name}".lower(),
                    f"{path}.{f.name} : {field_type(f)}",
                    "field",
                    desc.name,
                )
            )
        for et in desc.enum_types:
            self._add_enum(et, seen)
        for nt in desc.nested_types:
            self._add_message(nt, seen)

    def _add_enum(self, et, seen: set) -> None:
        if et.full_name in seen:
            return
        seen.add(et.full_name)
        self.enums.setdefault(et.name.lower(), []).append(et)
        self.by_path[access_path(et.full_name).lower()] = ("enum", et)
        self.search.append(
            (et.name.lower(), access_path(et.full_name), "enum", et.name)
        )
        for v in et.values:
            self.search.append(
                (v.name.lower(), f"{et.name}.{v.name}", "enum_value", et.name)
            )


_INDEX: dict[str, Index] = {}


def get_index(product: str) -> Index:
    """Build (once per product) and return the cached API index."""
    cached = _INDEX.get(product)
    if cached is not None:
        return cached

    idx = Index(product)
    try:
        import sys

        # Importing the product package runs its __init__, which eagerly imports
        # every subpackage — that is what makes the sys.modules sweep below complete.
        importlib.import_module(f"dlubal.api.{product}")
        import dlubal.api.common  # noqa: F401  (shared types: CoordinateSystemRepresentation, Vector3d, ObjectId, …)
        from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper

        # Index the product's objects AND the common shared types its fields reference
        # (e.g. a MemberLoad.coordinate_system -> common.CoordinateSystemRepresentation,
        # whose nested CoordinateSystemType enum carries COORDINATE_SYSTEM_TYPE_LOCAL).
        _ROOTS = (f"dlubal.api.{product}", "dlubal.api.common")
        seen: set = set()
        for modname, mod in list(sys.modules.items()):
            if mod is None or not modname.startswith(_ROOTS):
                continue
            for _, obj in list(vars(mod).items()):
                try:
                    if isinstance(obj, type):
                        d = getattr(obj, "DESCRIPTOR", None)
                        if isinstance(d, Descriptor):
                            idx._add_message(d, seen)
                    elif isinstance(obj, EnumTypeWrapper):
                        idx._add_enum(obj.DESCRIPTOR, seen)
                except Exception:
                    logger.debug("descriptor unavailable", exc_info=True)
                    continue
        # Application method signatures — so the model can look up create_model,
        # set_base_data(*, base_data=...) [keyword-only], calculate_all, etc.,
        # instead of guessing positional-vs-keyword args.
        try:
            import inspect as _pyinspect

            _pkg = importlib.import_module(f"dlubal.api.{product}")
            app_cls = getattr(_pkg, "Application", None)
            if app_cls is not None:
                for mname, member in _pyinspect.getmembers(
                    app_cls, predicate=_pyinspect.isfunction
                ):
                    if mname.startswith("_"):
                        continue
                    try:
                        sig = str(_pyinspect.signature(member))
                    except (ValueError, TypeError):
                        sig = "(...)"
                    sig = (
                        sig.replace("(self, ", "(")
                        .replace("(self)", "()")
                        .replace(_PREFIX, "")
                    )
                    doc = (member.__doc__ or "").strip().splitlines()
                    summary = doc[0].strip() if doc else ""
                    idx.methods[mname.lower()] = (mname, sig, summary)
                    idx.search.append(
                        (mname.lower(), f"app.{mname}{sig}", "method", mname)
                    )
        except Exception:
            logger.exception(
                "%s api index: Application method introspection failed",
                product,
            )

        # Read/navigation helpers for results & tree tables — plain functions/classes
        # (NOT protobuf), so index them explicitly: common.tree_table.* (get_value_by_path,
        # get_values_by_key, set_value_by_path, …) and the common.table.Table container.
        try:
            import inspect as _pyinspect

            def _doc1(o):
                d = (o.__doc__ or "").strip().splitlines()
                return d[0].strip() if d else ""

            def _hsig(o):
                try:
                    return (
                        str(_pyinspect.signature(o))
                        .replace("(self, ", "(")
                        .replace("(self)", "()")
                        .replace(_PREFIX, "")
                    )
                except (ValueError, TypeError):
                    return "(...)"

            for modname in ("dlubal.api.common.tree_table", "dlubal.api.common.table"):
                mod = importlib.import_module(modname)
                short = modname[len(_PREFIX) :]
                for fname, fn in _pyinspect.getmembers(mod, _pyinspect.isfunction):
                    if (
                        fname.startswith("_")
                        or getattr(fn, "__module__", "") != modname
                    ):
                        continue
                    idx.helpers[fname.lower()] = (
                        f"{short}.{fname}",
                        _hsig(fn),
                        _doc1(fn),
                    )
                    idx.search.append(
                        (
                            f"{short} {fname}".lower(),
                            f"{short}.{fname}{_hsig(fn)}",
                            "helper",
                            fname,
                        )
                    )
                for cname, cls in _pyinspect.getmembers(mod, _pyinspect.isclass):
                    if (
                        cname.startswith("_")
                        or getattr(cls, "__module__", "") != modname
                    ):
                        continue
                    meths = [
                        m
                        for m, _ in _pyinspect.getmembers(cls, _pyinspect.isfunction)
                        if not m.startswith("_")
                    ]
                    for m, mfn in _pyinspect.getmembers(cls, _pyinspect.isfunction):
                        if not m.startswith("_"):
                            idx.helpers[f"{cname}.{m}".lower()] = (
                                f"{short}.{cname}.{m}",
                                _hsig(mfn),
                                _doc1(mfn),
                            )
                    idx.helpers.setdefault(
                        cname.lower(),
                        (
                            f"{short}.{cname}",
                            "",
                            "class - methods: "
                            + ", ".join(meths)
                            + " (.data = pandas DataFrame)",
                        ),
                    )
                    idx.search.append(
                        (
                            f"{short} {cname}".lower(),
                            f"{short}.{cname} - methods: {', '.join(meths)}",
                            "helper",
                            cname,
                        )
                    )
        except Exception:
            logger.exception("%s api index: helper introspection failed", product)

        logger.info(
            "%s api index: %d objects, %d enums, %d methods, %d helpers, %d search entries",
            product,
            len(idx.objects),
            len(idx.enums),
            len(idx.methods),
            len(idx.helpers),
            len(idx.search),
        )
    except Exception as exc:
        idx.error = (
            f"Could not import dlubal.api.{product} for introspection: {exc}. "
            f"Is {product.upper()} installed on this machine?"
        )
        logger.exception("%s api index build failed", product)

    _INDEX[product] = idx
    return idx
