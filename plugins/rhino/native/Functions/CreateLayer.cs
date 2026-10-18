using System.Text.Json;
using System.Drawing;
using Rhino.DocObjects;

namespace RhinoMcpBridge.Functions;

internal static class CreateLayer
{
    [McpCommand("create_layer", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Create(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Create MCP layer", () =>
        {
            var name = NativeFunctions.Text(parameters, "name");
            if (string.IsNullOrWhiteSpace(name))
            {
                var suffix = 1;
                do
                {
                    name = $"Layer {suffix++}";
                }
                while (context.RhinoDocument.Layers.Any(layer => !layer.IsDeleted && string.Equals(layer.Name, name, StringComparison.OrdinalIgnoreCase)));
            }

            if (context.RhinoDocument.Layers.Any(layer => !layer.IsDeleted && string.Equals(layer.Name, name, StringComparison.OrdinalIgnoreCase)))
            {
                throw new NativeCommandException("layerAlreadyExists", "A Rhino layer with that name already exists.");
            }

            var layer = new Layer { Name = name };
            var color = NativeFunctions.Property(parameters, "color");
            if (color.ValueKind == JsonValueKind.Array)
            {
                var channels = color.EnumerateArray().Select(NativeFunctions.ReadFiniteNumber).ToArray();
                if (channels.Length != 3 || channels.Any(value => value < 0 || value > 255 || Math.Truncate(value) != value))
                {
                    throw new NativeCommandException("invalidParameters", "RGB color channels must be integers from 0 to 255.");
                }

                layer.Color = Color.FromArgb((int)channels[0], (int)channels[1], (int)channels[2]);
            }

            if (NativeFunctions.Has(parameters, "parent"))
            {
                var parentName = NativeFunctions.Text(parameters, "parent");
                if (string.IsNullOrWhiteSpace(parentName))
                {
                    throw new NativeCommandException("invalidParameters", "The parent layer name is invalid.");
                }

                var parents = context.RhinoDocument.Layers
                    .Where(item => !item.IsDeleted && (string.Equals(item.FullPath, parentName, StringComparison.OrdinalIgnoreCase) || string.Equals(item.Name, parentName, StringComparison.OrdinalIgnoreCase)))
                    .Take(2)
                    .ToArray();
                if (parents.Length != 1)
                {
                    throw new NativeCommandException(parents.Length == 0 ? "layerNotFound" : "ambiguousLayerName", "The parent layer must identify exactly one Rhino layer.");
                }

                layer.ParentLayerId = parents[0].Id;
            }

            var index = context.RhinoDocument.Layers.Add(layer);
            if (index < 0)
            {
                throw new NativeCommandException("layerCreateFailed", "Rhino could not create the layer.");
            }

            return NativeFunctions.LayerInfo(context.RhinoDocument.Layers[index]);
        });
}
