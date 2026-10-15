using System.Text.Json;
using Rhino.DocObjects;

namespace RhinoMcpBridge.Functions;

internal static class ObjectAttributesCommands
{
    [McpCommand("get_object_attributes", ReadOnly = true, SupportsDryRun = false)]
    internal static object Get(RequestContext context, JsonElement parameters)
    {
        var obj = NativeFunctions.RequireObject(context, parameters);
        var attributes = obj.Attributes;
        var layer = context.RhinoDocument.Layers.FindIndex(attributes.LayerIndex);
        if (layer is null)
        {
            throw new NativeCommandException("layerNotFound", "The Rhino object's layer was not found.");
        }
        var strings = attributes.GetUserStrings();
        var userStrings = new SortedDictionary<string, string>(StringComparer.Ordinal);
        if (strings is not null)
        {
            foreach (var key in strings.AllKeys.Where(key => key is not null))
            {
                userStrings[key!] = strings[key!] ?? string.Empty;
            }
        }

        return new Dictionary<string, object?>
        {
            ["id"] = obj.Id.ToString("D"),
            ["name"] = string.IsNullOrWhiteSpace(attributes.Name) ? "(unnamed)" : attributes.Name,
            ["type"] = NativeFunctions.GeometryType(obj.Geometry),
            ["layer"] = new Dictionary<string, object?>
            {
                ["index"] = layer.Index,
                ["id"] = layer.Id.ToString("D"),
                ["name"] = layer.Name,
                ["full_path"] = layer.FullPath,
            },
            ["color"] = NativeFunctions.RgbObject(attributes.ObjectColor),
            ["color_source"] = attributes.ColorSource.ToString(),
            ["material_index"] = attributes.MaterialIndex,
            ["material_source"] = attributes.MaterialSource.ToString(),
            ["visible"] = attributes.Visible,
            ["locked"] = obj.IsLocked,
            ["hidden"] = obj.IsHidden,
            ["normal"] = obj.IsNormal,
            ["user_strings"] = userStrings,
        };
    }

    [McpCommand("update_object_attributes", ReadOnly = false, SupportsDryRun = false)]
    internal static object Update(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Update MCP object attributes", () =>
        {
            var obj = NativeFunctions.RequireObject(context, parameters);
            var attributes = obj.Attributes.Duplicate();
            if (NativeFunctions.Has(parameters, "new_name"))
            {
                attributes.Name = NativeFunctions.Text(parameters, "new_name") ?? string.Empty;
            }

            if (NativeFunctions.Has(parameters, "layer"))
            {
                var layerName = NativeFunctions.Text(parameters, "layer");
                var layerParameters = JsonDocument.Parse(JsonSerializer.Serialize(new { name = layerName })).RootElement;
                attributes.LayerIndex = NativeFunctions.RequireLayer(context, layerParameters).Index;
            }

            var color = NativeFunctions.Property(parameters, "color");
            if (color.ValueKind == JsonValueKind.Array)
            {
                var colorParameters = JsonDocument.Parse(JsonSerializer.Serialize(new { color = color.Clone() })).RootElement;
                var colorAttributes = NativeFunctions.Attributes(context, colorParameters);
                attributes.ObjectColor = colorAttributes.ObjectColor;
                attributes.ColorSource = ObjectColorSource.ColorFromObject;
            }

            if (NativeFunctions.Has(parameters, "material_index"))
            {
                var index = NativeFunctions.Integer(parameters, "material_index", -2);
                if (index < -1 || index >= context.RhinoDocument.Materials.Count)
                {
                    throw new NativeCommandException("invalidParameters", "The material index is outside the document material table.");
                }

                attributes.MaterialIndex = index;
                attributes.MaterialSource = index < 0 ? ObjectMaterialSource.MaterialFromLayer : ObjectMaterialSource.MaterialFromObject;
            }

            if (NativeFunctions.Has(parameters, "visible"))
            {
                attributes.Visible = NativeFunctions.Boolean(parameters, "visible");
            }

            if (NativeFunctions.Has(parameters, "locked"))
            {
                attributes.Mode = NativeFunctions.Boolean(parameters, "locked") ? ObjectMode.Locked : ObjectMode.Normal;
            }

            if (NativeFunctions.Boolean(parameters, "clear_user_strings"))
            {
                attributes.DeleteAllUserStrings();
            }

            foreach (var key in NativeFunctions.Property(parameters, "delete_user_strings").ValueKind == JsonValueKind.Array
                ? NativeFunctions.Property(parameters, "delete_user_strings").EnumerateArray().Select(value => value.GetString()!).ToArray()
                : Array.Empty<string>())
            {
                attributes.DeleteUserString(key);
            }

            var updates = NativeFunctions.Property(parameters, "user_strings");
            if (updates.ValueKind == JsonValueKind.Object)
            {
                foreach (var update in updates.EnumerateObject())
                {
                    if (update.Value.ValueKind == JsonValueKind.Null)
                    {
                        attributes.DeleteUserString(update.Name);
                    }
                    else
                    {
                        attributes.SetUserString(update.Name, update.Value.ValueKind == JsonValueKind.String
                            ? update.Value.GetString() ?? string.Empty
                            : update.Value.ToString());
                    }
                }
            }

            if (!context.RhinoDocument.Objects.ModifyAttributes(obj, attributes, quiet: true))
            {
                throw new NativeCommandException("attributeUpdateFailed", "Rhino could not update the object attributes.");
            }

            return Get(context, parameters);
        });
}
