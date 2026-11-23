using System.Diagnostics;
using System.Text.Json;
using Grasshopper.Kernel;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperSolution
{
    [McpCommand("gh_run_solution", ReadOnly = false, SupportsDryRun = false)]
    internal static object RunSolution(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var timer = Stopwatch.StartNew();
        GrasshopperOperations.Recompute(document, NativeFunctions.Boolean(parameters, "expire_all"));
        var activeObjects = document.Objects.OfType<IGH_ActiveObject>().ToArray();
        var errors = MessagesAtLevel(activeObjects, GH_RuntimeMessageLevel.Error);
        var warnings = MessagesAtLevel(activeObjects, GH_RuntimeMessageLevel.Warning);
        return new Dictionary<string, object?>
        {
            ["success"] = errors.Length == 0,
            ["error_count"] = errors.Length,
            ["warning_count"] = warnings.Length,
            ["errors"] = errors,
            ["warnings"] = warnings,
            ["solution_state"] = document.SolutionState.ToString(),
            ["duration_ms"] = timer.ElapsedMilliseconds,
            ["message"] = errors.Length > 0
                ? $"Solution has {errors.Length} error(s)"
                : warnings.Length > 0
                    ? $"Solution completed with {warnings.Length} warning(s)"
                    : "Solution completed successfully",
        };
    }

    private static Dictionary<string, object?>[] MessagesAtLevel(
        IEnumerable<IGH_ActiveObject> objects, GH_RuntimeMessageLevel level) =>
        objects.Where(item => item.RuntimeMessageLevel == level)
            .Select(item => new Dictionary<string, object?>
            {
                ["instance_id"] = item.InstanceGuid.ToString("D"),
                ["name"] = item.Name,
                ["nickname"] = item.NickName,
                ["category"] = item.Category,
                ["messages"] = item.RuntimeMessages(level).ToArray(),
            }).ToArray();

    [McpCommand("gh_expire_solution", ReadOnly = false, SupportsDryRun = false)]
    internal static object ExpireSolution(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var targets = new List<IGH_DocumentObject>();
        var selectedIds = NativeFunctions.Property(parameters, "component_ids");
        if (selectedIds.ValueKind == JsonValueKind.Array && selectedIds.GetArrayLength() > 0)
        {
            foreach (var id in NativeFunctions.Ids(parameters, "component_ids"))
            {
                if (document.FindObject(id, true) is { } item)
                    targets.Add(item);
            }
        }
        else if (NativeFunctions.Has(parameters, "instance_id") || NativeFunctions.Has(parameters, "nickname"))
        {
            var item = GrasshopperHelpers.RequireObject(document, parameters);
            targets.Add(item);
        }
        else
        {
            targets.AddRange(document.Objects);
        }

        var expireDownstream = NativeFunctions.Boolean(parameters, "expire_downstream", true);
        var explicitTarget = selectedIds.ValueKind == JsonValueKind.Array && selectedIds.GetArrayLength() > 0
            || NativeFunctions.Has(parameters, "instance_id")
            || NativeFunctions.Has(parameters, "nickname");
        foreach (var item in targets)
        {
            item.ExpireSolution(explicitTarget && expireDownstream);
        }

        var recompute = NativeFunctions.Boolean(parameters, "recompute");
        if (recompute)
        {
            GrasshopperOperations.Recompute(document, expireAll: false);
        }

        return new Dictionary<string, object?>
        {
            ["expired_count"] = targets.Count,
            ["recomputed"] = recompute,
            ["message"] = $"Expired {targets.Count} Grasshopper object(s)" + (recompute ? " and triggered recompute" : ""),
        };
    }

    internal static object SolutionSummary(GH_Document document)
    {
        var activeObjects = document.Objects.OfType<IGH_ActiveObject>().ToArray();
        var messages = activeObjects.SelectMany(active =>
        {
            var owner = (IGH_DocumentObject)active;
            return new[]
                {
                    GH_RuntimeMessageLevel.Warning,
                    GH_RuntimeMessageLevel.Error,
                }
                .SelectMany(level => active.RuntimeMessages(level).Select(message => new Dictionary<string, object?>
                {
                    ["instance_id"] = owner.InstanceGuid.ToString("D"),
                    ["nickname"] = owner.NickName,
                    ["level"] = level.ToString(),
                    ["message"] = message,
                }));
        }).ToArray();

        return new Dictionary<string, object?>
        {
            ["document_id"] = document.DocumentID.ToString("D"),
            ["solution_state"] = document.SolutionState.ToString(),
            ["solution_duration_ms"] = document.SolutionSpan.TotalMilliseconds,
            ["object_count"] = document.ObjectCount,
            ["warning_count"] = messages.Count(message => (string)message["level"]! == GH_RuntimeMessageLevel.Warning.ToString()),
            ["error_count"] = messages.Count(message => (string)message["level"]! == GH_RuntimeMessageLevel.Error.ToString()),
            ["runtime_messages"] = messages,
        };
    }
}
