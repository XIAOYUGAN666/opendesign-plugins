using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class CreateObjects
{
    [McpCommand("create_objects", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object CreateMany(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Create MCP geometry batch", () =>
        {
            var results = new Dictionary<string, object?>();
            var successCount = 0;
            var errors = new List<Dictionary<string, object?>>();
            foreach (var entry in parameters.EnumerateObject())
            {
                context.CancellationToken.ThrowIfCancellationRequested();
                try
                {
                    results[entry.Name] = CreateObject.CreateGeometry(context, entry.Value);
                    successCount++;
                }
                catch (Exception exception) when (exception is not OperationCanceledException)
                {
                    var error = exception is NativeCommandException native ? native.SafeMessage : "Geometry could not be created.";
                    results[entry.Name] = new Dictionary<string, object?>
                    {
                        ["status"] = "error",
                        ["error"] = error,
                    };
                    errors.Add(new Dictionary<string, object?>
                    {
                        ["name"] = entry.Name,
                        ["error"] = error,
                    });
                }
            }

            return new Dictionary<string, object?>
            {
                ["success_count"] = successCount,
                ["failure_count"] = errors.Count,
                ["total"] = successCount + errors.Count,
                ["objects"] = results,
                ["errors"] = errors,
            };
        });
}
