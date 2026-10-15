using System.Text.Json;
using Grasshopper.Kernel;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperSolution
{
    [McpCommand("gh_run_solution", ReadOnly = false, SupportsDryRun = false)]
    internal static object RunSolution(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        GrasshopperOperations.Recompute(document, NativeFunctions.Boolean(parameters, "expire_all"));
        return SolutionSummary(document);
    }

    [McpCommand("gh_expire_solution", ReadOnly = false, SupportsDryRun = false)]
    internal static object ExpireSolution(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var targets = new Dictionary<Guid, IGH_DocumentObject>();
        var selectedIds = NativeFunctions.Property(parameters, "component_ids");
        if (selectedIds.ValueKind == JsonValueKind.Array)
        {
            foreach (var id in NativeFunctions.Ids(parameters, "component_ids"))
            {
                var item = document.Objects.FirstOrDefault(candidate => candidate.InstanceGuid == id)
                    ?? throw new NativeCommandException("componentNotFound", "A Grasshopper object to expire was not found.");
                targets[id] = item;
            }
        }

        if (NativeFunctions.Has(parameters, "instance_id") || NativeFunctions.Has(parameters, "nickname"))
        {
            var item = GrasshopperHelpers.RequireObject(document, parameters);
            targets[item.InstanceGuid] = item;
        }

        var expireAll = targets.Count == 0;
        if (expireAll)
        {
            targets = document.Objects.ToDictionary(item => item.InstanceGuid, item => item);
        }

        var expireDownstream = NativeFunctions.Boolean(parameters, "expire_downstream", true);
        foreach (var item in targets.Values.OfType<IGH_ActiveObject>())
        {
            item.ExpireSolution(expireDownstream);
        }

        var recompute = NativeFunctions.Boolean(parameters, "recompute");
        if (recompute)
        {
            document.NewSolution(expireAll);
        }

        return new Dictionary<string, object?>
        {
            ["expired_count"] = targets.Count,
            ["expired_ids"] = targets.Keys.Select(id => id.ToString("D")).ToArray(),
            ["expire_downstream"] = expireDownstream,
            ["recomputed"] = recompute,
            ["solution"] = SolutionSummary(document),
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
