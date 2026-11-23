using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class GetObjectInfo
{
    [McpCommand("get_object_info", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Get(RequestContext context, JsonElement parameters)
    {
        var obj = NativeFunctions.RequireObject(context, parameters);
        var result = NativeFunctions.ObjectInfo(obj);
        result["attributes"] = ObjectAttributesCommands.UserStrings(obj.Attributes);
        return result;
    }
}
