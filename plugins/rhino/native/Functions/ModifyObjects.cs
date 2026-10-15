using System.Text.Json;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class ModifyObjects
{
    [McpCommand("modify_objects", ReadOnly = false, SupportsDryRun = false)]
    internal static object ModifyMany(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Modify MCP object batch", () =>
        {
            var items = NativeFunctions.Property(parameters, "objects");
            if (items.ValueKind != JsonValueKind.Array)
            {
                throw new NativeCommandException("invalidParameters", "Object modifications must be an array.");
            }

            var all = NativeFunctions.Boolean(parameters, "all");
            var requested = items.EnumerateArray().ToArray();
            var targets = all ? NativeFunctions.Objects(context).ToArray() : Array.Empty<RhinoObject>();
            if (all && requested.Length > 0)
            {
                requested = targets.Select(_ => requested[0]).ToArray();
            }

            var succeeded = 0;
            var errors = new List<Dictionary<string, object?>>();
            foreach (var item in requested)
            {
                try
                {
                    var obj = all
                        ? targets[succeeded + errors.Count]
                        : NativeFunctions.RequireObject(context, item);
                    Apply(context, obj, item);
                    succeeded++;
                }
                catch (Exception exception)
                {
                    errors.Add(new Dictionary<string, object?>
                    {
                        ["id"] = NativeFunctions.Text(item, "id") ?? NativeFunctions.Text(item, "name"),
                        ["error"] = exception is NativeCommandException native ? native.SafeMessage : "The object could not be modified.",
                    });
                }
            }

            return new Dictionary<string, object?>
            {
                ["success_count"] = succeeded,
                ["failure_count"] = errors.Count,
                ["total"] = requested.Length,
                ["errors"] = errors,
            };
        });

    private static void Apply(RequestContext context, RhinoObject obj, JsonElement parameters)
    {
        if (NativeFunctions.Has(parameters, "translation") || NativeFunctions.Has(parameters, "rotation") || NativeFunctions.Has(parameters, "scale"))
        {
            var geometry = obj.Geometry.Duplicate();
            NativeFunctions.ApplyModifyTransforms(geometry, parameters, obj.Geometry.GetBoundingBox(accurate: true));
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
    }
}
