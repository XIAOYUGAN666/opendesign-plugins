using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class DeleteObject
{
    [McpCommand("delete_object", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Delete(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Delete MCP object", () =>
        {
            var all = NativeFunctions.Boolean(parameters, "all");
            if (all && (ViewportNavigation.Given(parameters, "id") || ViewportNavigation.Given(parameters, "name")))
                throw new NativeCommandException("invalid_argument", "all cannot be combined with id or name.");
            var targets = all
                ? NativeFunctions.Objects(context).ToArray()
                : new[] { NativeFunctions.RequireObject(context, parameters) };
            // Rhino invalidates object metadata after deletion. Capture the
            // receipt identity before the first engineering mutation.
            var targetIds = targets.Select(target => target.Id).ToArray();
            var name = all ? null : targets[0].Attributes.Name ?? "(unnamed)";
            var deleted = 0;
            var errors = new List<string>();
            foreach (var targetId in targetIds)
            {
                if (context.RhinoDocument.Objects.Delete(targetId, quiet: true))
                {
                    deleted++;
                }
                else
                {
                    errors.Add(targetId.ToString("D"));
                }
            }

            if (all)
            {
                var result = new Dictionary<string, object?>
                {
                    ["deleted"] = true,
                    ["count"] = deleted,
                    ["scope"] = "all",
                };
                if (errors.Count > 0)
                    result["skipped_count"] = errors.Count;
                return result;
            }
            if (deleted == 0)
                throw new NativeCommandException("objectDeleteFailed", "Rhino could not delete the requested object.");

            return new Dictionary<string, object?>
            {
                ["deleted"] = true,
                ["id"] = targetIds[0].ToString("D"),
                ["name"] = name,
            };
        });
}
