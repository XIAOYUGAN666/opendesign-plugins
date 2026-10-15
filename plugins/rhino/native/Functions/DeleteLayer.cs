using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class DeleteLayer
{
    [McpCommand("delete_layer", ReadOnly = false, SupportsDryRun = false)]
    internal static object Delete(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Delete MCP layer", () =>
        {
            var layer = NativeFunctions.RequireLayer(context, parameters);
            if (layer.Index == context.RhinoDocument.Layers.CurrentLayerIndex)
            {
                throw new NativeCommandException("currentLayer", "The current Rhino layer cannot be deleted.");
            }

            var deleted = context.RhinoDocument.Layers.Delete(layer.Index, quiet: true);
            if (!deleted)
            {
                throw new NativeCommandException("layerDeleteFailed", "Rhino could not delete the layer; it may contain objects or child layers.");
            }

            return new Dictionary<string, object?>
            {
                ["id"] = layer.Id.ToString("D"),
                ["name"] = layer.FullPath,
                ["deleted"] = true,
                ["count"] = 1,
                ["scope"] = "layer",
            };
        });
}
