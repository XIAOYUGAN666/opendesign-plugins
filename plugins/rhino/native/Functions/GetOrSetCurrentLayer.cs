using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class GetOrSetCurrentLayer
{
    [McpCommand("get_or_set_current_layer", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object GetOrSet(RequestContext context, JsonElement parameters)
    {
        var layers = context.RhinoDocument.Layers;
        if (NativeFunctions.Has(parameters, "guid") || NativeFunctions.Has(parameters, "name"))
        {
            return NativeFunctions.Mutate(context, "Set MCP current layer", () =>
            {
                var layer = NativeFunctions.RequireLayer(context, parameters);
                if (!layers.SetCurrentLayerIndex(layer.Index, quiet: true))
                {
                    throw new NativeCommandException("layerSetFailed", "Rhino could not set the current layer.");
                }

                return NativeFunctions.LayerInfo(layer);
            });
        }

        var current = layers.FindIndex(layers.CurrentLayerIndex)
            ?? throw new NativeCommandException("layerNotFound", "Rhino has no current layer.");
        return NativeFunctions.LayerInfo(current);
    }
}
