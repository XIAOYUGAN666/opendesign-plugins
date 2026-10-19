using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class DeleteObject
{
    [McpCommand("delete_object", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Delete(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Delete MCP object", () =>
        {
            var all = NativeFunctions.Boolean(parameters, "all");
            var targets = all
                ? NativeFunctions.Objects(context).ToArray()
                : new[] { NativeFunctions.RequireObject(context, parameters) };
            // Rhino invalidates object metadata after deletion. Capture the
            // receipt identity before the first engineering mutation.
            var targetIds = targets.Select(target => target.Id).ToArray();
            var name = all ? null : targets[0].Attributes.Name ?? string.Empty;
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

            if (deleted == 0 && errors.Count > 0)
            {
                throw new NativeCommandException("objectDeleteFailed", "Rhino could not delete the requested object.");
            }

            var result = new Dictionary<string, object?>
            {
                ["deleted"] = deleted > 0,
                ["count"] = deleted,
                ["failed_ids"] = errors,
            };
            if (all)
            {
                result["scope"] = "all";
            }
            else
            {
                result["id"] = targetIds[0].ToString("D");
                result["name"] = name;
            }
            return result;
        });
}
