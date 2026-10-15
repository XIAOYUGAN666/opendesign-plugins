"""AST-based sandbox validator and safe builtins for script execution."""

import ast

FORBIDDEN_AST_NODES = (ast.Import, ast.ImportFrom)

FORBIDDEN_NAMES = frozenset(
    {
        "__import__",
        "eval",
        "compile",
        "exec",
        "execfile",
        "globals",
        "locals",
        "__class__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__code__",
        "__builtins__",
        "breakpoint",
        "open",
        "input",
    }
)

SAFE_BUILTINS: dict = {
    "abs": abs,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "len": len,
    "float": float,
    "int": int,
    "list": list,
    "dict": dict,
    "enumerate": enumerate,
    "str": str,
    "bool": bool,
    "tuple": tuple,
    "set": set,
    "bytes": bytes,
    "type": type,
    "object": object,
    "round": round,
    "sorted": sorted,
    "zip": zip,
    "map": map,
    "filter": filter,
    "isinstance": isinstance,
    "hasattr": hasattr,
    "getattr": getattr,
    "setattr": setattr,
    "repr": repr,
    "True": True,
    "False": False,
    "None": None,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "RuntimeError": RuntimeError,
}


def validate_code_ast(code: str, available_objects: str = "app, math") -> str | None:
    """
    Parse code and reject forbidden constructs.
    Returns an error string if unsafe, None if safe.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e}"

    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_AST_NODES):
            return f"Import statements are disabled. Use the pre-injected objects: {available_objects}."
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return f"Access to '{node.id}' is not allowed."
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("_") or node.attr in {"format", "format_map"}
        ):
            return f"Access to '{node.attr}' is not allowed."

    return None


def script_builtins(print_function):
    """Reduce accidental escape paths; this is not OS isolation for hostile code."""

    def public_getattr(obj, name, *default):
        if (
            not isinstance(name, str)
            or name.startswith("_")
            or name in {"format", "format_map"}
        ):
            raise ValueError("Private or reflective attribute access is disabled")
        return getattr(obj, name, *default)

    def public_setattr(obj, name, value):
        public_getattr(obj, name, None)
        return setattr(obj, name, value)

    result = SAFE_BUILTINS.copy()
    result.update(
        print=print_function,
        getattr=public_getattr,
        setattr=public_setattr,
        hasattr=lambda obj, name: public_getattr(obj, name, None) is not None,
    )
    return result


def execute_script(code, namespace, *, preamble=""):
    """Shared privileged boundary; preamble is supplied only by the host.

    Consolidates the existing product executors. AST checks reduce accidental
    misuse but are not an OS sandbox; script tools require explicit enablement.
    """
    error = validate_code_ast(code)
    if error:
        raise ValueError(error)
    exec(preamble + code, namespace, namespace)  # noqa: S102
