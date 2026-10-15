using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class DeleteObject
{
    [McpCommand("delete_object", ReadOnly = false, SupportsDryRun = false)]
    internal static object Delete(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Delete MCP object", () =>
        {
            var all = NativeFunctions.Boolean(parameters, "all");
            var targets = all
                ? NativeFunctions.Objects(context).ToArray()
                : new[] { NativeFunctions.RequireObject(context, parameters) };
            var deleted = 0;
            var errors = new List<string>();
            foreach (var target in targets)
            {
                if (context.RhinoDocument.Objects.Delete(target.Id, quiet: true))
                {
                    deleted++;
                }
                else
                {
                    errors.Add(target.Id.ToString("D"));
                }
            }

            if (deleted == 0 && errors.Count > 0)
            {
                throw new NativeCommandException("objectDeleteFailed", "Rhino could not delete the requested object.");
            }

            return new Dictionary<string, object?>
            {
                ["id"] = all ? null : targets[0].Id.ToString("D"),
                ["name"] = all ? null : targets[0].Attributes.Name,
                ["deleted"] = deleted > 0,
                ["count"] = deleted,
                ["scope"] = all ? "document" : "object",
                ["failed_ids"] = errors,
            };
        });
}
