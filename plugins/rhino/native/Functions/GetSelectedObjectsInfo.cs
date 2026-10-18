using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class GetSelectedObjectsInfo
{
    [McpCommand("get_selected_objects_info", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Get(RequestContext context, JsonElement parameters)
    {
        var includeAttributes = NativeFunctions.Boolean(parameters, "include_attributes", true);
        return NativeFunctions.SelectedIds(context)
            .Select(id => NativeFunctions.FindObject(context, id))
            .Where(obj => obj is not null)
            .Select(obj =>
            {
                var info = NativeFunctions.ObjectInfo(obj!);
                if (includeAttributes)
                {
                    info["attributes"] = new Dictionary<string, object?>
                    {
                        ["name"] = obj!.Attributes.Name,
                        ["layer_index"] = obj.Attributes.LayerIndex,
                        ["visible"] = obj.Attributes.Visible,
                        ["locked"] = obj.IsLocked,
                        ["color"] = NativeFunctions.Rgb(obj.Attributes.ObjectColor),
                    };
                }

                return info;
            })
            .ToArray();
    }
}
