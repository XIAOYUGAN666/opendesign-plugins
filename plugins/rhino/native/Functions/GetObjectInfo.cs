using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class GetObjectInfo
{
    [McpCommand("get_object_info", ReadOnly = true, SupportsDryRun = false)]
    internal static object Get(RequestContext context, JsonElement parameters) =>
        NativeFunctions.ObjectInfo(NativeFunctions.RequireObject(context, parameters));
}
