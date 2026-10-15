using System.Text.Json;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class CreateObject
{
    [McpCommand("create_object", ReadOnly = false, SupportsDryRun = false)]
    internal static object Create(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Create MCP geometry", () =>
        {
            var type = NativeFunctions.Text(parameters, "type")
                ?? throw new NativeCommandException("invalidParameters", "A geometry type is required.");
            var geometry = NativeFunctions.CreateGeometry(context, type, NativeFunctions.Property(parameters, "params"));
            NativeFunctions.ApplyTransforms(geometry, parameters, Point3d.Origin);
            var attributes = NativeFunctions.Attributes(context, parameters);
            var id = NativeFunctions.AddGeometry(context, geometry, attributes);
            var created = context.RhinoDocument.Objects.FindId(id)
                ?? throw new NativeCommandException("geometryAddFailed", "Rhino added geometry but could not read it back.");
            var result = NativeFunctions.ObjectInfo(created);
            result["success"] = true;
            result["message"] = "Geometry created.";
            return result;
        });
}
