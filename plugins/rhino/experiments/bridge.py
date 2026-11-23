"""Run reviewed RhinoCommon measurement code against an explicit test Bridge."""

import json
from contextlib import contextmanager
from contextvars import ContextVar

from rhino_mcp.instances import resolve_target
from rhino_mcp.server import RhinoConnection
from rhino_mcp.stdio_bridge import await_bridge

_client = ContextVar("experiment_bridge", default=None)


@contextmanager
def use_bridge(port):
    with await_bridge(resolve_target(port), timeout=45) as bridge:
        token = _client.set(RhinoConnection(bridge))
        try:
            yield
        finally:
            _client.reset(token)


def script(code):
    client = _client.get()
    if client is None:
        raise RuntimeError("Select the dedicated test Bridge before measuring")
    serialize = """
string Serialize(object value) {
    var type = AppDomain.CurrentDomain.GetAssemblies()
        .Select(a => a.GetType("Newtonsoft.Json.JsonConvert"))
        .First(t => t != null);
    return (string)type.GetMethod("SerializeObject", new[] { typeof(object) })
        .Invoke(null, new[] { value });
}
"""
    result = client.send_command("execute_rhinocommon_csharp_code", {"code": serialize + code})
    if not result.get("success"):
        raise RuntimeError("RhinoCommon measurement failed")
    return result["output"].strip()


def assert_document(serial, marker):
    state = json.loads(script("""
output.AppendLine(Serialize(new {
    document = doc.RuntimeSerialNumber,
    marker = doc.Strings.GetValue("rhinomcp_experiment")
}));
"""))
    if state["document"] != serial or state["marker"] != marker:
        raise RuntimeError("Active Rhino test document changed")
    return state
