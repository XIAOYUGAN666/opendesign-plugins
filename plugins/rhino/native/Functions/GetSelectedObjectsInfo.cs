using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class GetSelectedObjectsInfo
{
    [McpCommand("get_selected_objects_info", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Get(RequestContext context, JsonElement parameters)
    {
        var includeAttributes = NativeFunctions.Boolean(parameters, "include_attributes");
        var selected = NativeFunctions.SelectedIds(context)
            .Select(id => NativeFunctions.FindObject(context, id))
            .Where(obj => obj is not null)
            .Select(obj =>
            {
                var info = NativeFunctions.ObjectInfo(obj!);
                if (includeAttributes)
                {
                    info["attributes"] = ObjectAttributesCommands.UserStrings(obj!.Attributes);
                }

                return info;
            })
            .ToArray();
        return new Dictionary<string, object?> { ["selected_objects"] = selected };
    }
}
