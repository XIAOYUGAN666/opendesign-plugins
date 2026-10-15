using System.Text.Json;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class ModifyObject
{
    [McpCommand("modify_object", ReadOnly = false, SupportsDryRun = false)]
    internal static object Modify(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Modify MCP object", () =>
        {
            var obj = NativeFunctions.RequireObject(context, parameters);
            if (NativeFunctions.Has(parameters, "translation") || NativeFunctions.Has(parameters, "rotation") || NativeFunctions.Has(parameters, "scale"))
            {
                var geometry = obj.Geometry.Duplicate();
                var originalBounds = obj.Geometry.GetBoundingBox(accurate: true);
                NativeFunctions.ApplyModifyTransforms(geometry, parameters, originalBounds);
                if (!context.RhinoDocument.Objects.Replace(obj.Id, geometry, ignoreModes: false))
                {
                    throw new NativeCommandException("geometryModifyFailed", "Rhino could not replace the object geometry.");
                }
            }

            var attributes = obj.Attributes.Duplicate();
            if (NativeFunctions.Has(parameters, "new_name"))
            {
                attributes.Name = NativeFunctions.Text(parameters, "new_name") ?? string.Empty;
            }

            if (NativeFunctions.TryColor(parameters, "new_color", out var color))
            {
                attributes.ObjectColor = color;
                attributes.ColorSource = ObjectColorSource.ColorFromObject;
            }

            if (NativeFunctions.Has(parameters, "visible"))
            {
                attributes.Visible = NativeFunctions.Boolean(parameters, "visible");
            }

            if ((NativeFunctions.Has(parameters, "new_name") || NativeFunctions.Has(parameters, "new_color") || NativeFunctions.Has(parameters, "visible"))
                && !context.RhinoDocument.Objects.ModifyAttributes(obj.Id, attributes, quiet: true))
            {
                throw new NativeCommandException("attributeUpdateFailed", "Rhino could not update the object attributes.");
            }

            var changed = context.RhinoDocument.Objects.FindId(obj.Id)
                ?? throw new NativeCommandException("objectNotFound", "The Rhino object disappeared during modification.");
            var result = NativeFunctions.ObjectInfo(changed);
            result["success"] = true;
            result["message"] = "Object modified.";
            return result;
        });
}
