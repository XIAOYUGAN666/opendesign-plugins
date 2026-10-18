using System.Text.Json;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class CreateObjects
{
    [McpCommand("create_objects", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object CreateMany(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Create MCP geometry batch", () =>
        {
            var created = new List<string>();
            var errors = new List<Dictionary<string, object?>>();
            foreach (var entry in parameters.EnumerateObject())
            {
                context.CancellationToken.ThrowIfCancellationRequested();
                try
                {
                    var item = entry.Value;
                    var type = NativeFunctions.Text(item, "type")
                        ?? throw new NativeCommandException("invalidParameters", "A geometry type is required.");
                    var geometry = NativeFunctions.CreateGeometry(context, type, NativeFunctions.Property(item, "params"));
                    NativeFunctions.ApplyTransforms(geometry, item, Point3d.Origin);
                    var attributes = NativeFunctions.Attributes(context, item);
                    if (string.IsNullOrWhiteSpace(attributes.Name))
                    {
                        attributes.Name = entry.Name;
                    }

                    created.Add(NativeFunctions.AddGeometry(context, geometry, attributes).ToString("D"));
                }
                catch (Exception exception)
                {
                    errors.Add(new Dictionary<string, object?>
                    {
                        ["name"] = entry.Name,
                        ["error"] = exception is NativeCommandException native ? native.SafeMessage : "Geometry could not be created.",
                    });
                }
            }

            return new Dictionary<string, object?>
            {
                ["success_count"] = created.Count,
                ["failure_count"] = errors.Count,
                ["total"] = created.Count + errors.Count,
                ["created_ids"] = created,
                ["errors"] = errors,
            };
        });
}
