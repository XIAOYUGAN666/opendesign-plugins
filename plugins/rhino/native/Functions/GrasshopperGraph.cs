using System.Text.Json;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperGraph
{
    [McpCommand("gh_get_graph", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetGraph(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var graphId = GrasshopperHelpers.RequireGraphId(parameters);
        var includeValues = NativeFunctions.Boolean(parameters, "include_values");
        var maxItems = Math.Max(0, Math.Min(1000, NativeFunctions.Integer(parameters, "max_items", 20)));
        var aliases = GrasshopperHelpers.GraphAliases(document, graphId);
        var objects = aliases
            .OrderBy(entry => entry.Key, StringComparer.Ordinal)
            .Select(entry => new Dictionary<string, object?>
            {
                ["alias"] = entry.Key,
                ["object"] = GrasshopperHelpers.ObjectInfo(entry.Value, includeValues, maxItems),
            })
            .ToArray();
        var groups = GrasshopperHelpers.FindGraphGroups(document, graphId);
        return new Dictionary<string, object?>
        {
            ["graph_id"] = graphId,
            ["objects"] = objects,
            ["components"] = objects,
            ["aliases"] = aliases.ToDictionary(
                entry => entry.Key,
                entry => entry.Value.InstanceGuid.ToString("D"),
                StringComparer.Ordinal
            ),
            ["groups"] = groups.Select(group => GrasshopperHelpers.ObjectInfo(group)).ToArray(),
            ["connections"] = ConnectionsForGraph(document, aliases.Values.ToArray()),
            ["object_count"] = objects.Length,
        };
    }

    [McpCommand("gh_clear_graph", ReadOnly = false, SupportsDryRun = false)]
    internal static object ClearGraph(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var graphId = GrasshopperHelpers.RequireGraphId(parameters);
        var includeGroups = NativeFunctions.Boolean(parameters, "include_groups", true);
        var objects = GrasshopperHelpers.GraphObjects(document, graphId).ToList();
        var groups = includeGroups ? GrasshopperHelpers.FindGraphGroups(document, graphId) : Array.Empty<GH_Group>();
        if (groups.Count > 0)
        {
            objects.AddRange(groups);
        }

        return GrasshopperOperations.Mutate(document, "Clear MCP Grasshopper graph", () =>
        {
            GrasshopperOperations.RemoveObjects(document, objects, "Clear MCP Grasshopper graph");
            if (includeGroups)
            {
                GrasshopperHelpers.ForgetGraph(document, graphId);
            }

            var recompute = NativeFunctions.Boolean(parameters, "recompute");
            if (recompute)
            {
                GrasshopperOperations.Recompute(document, expireAll: true);
            }

            return new Dictionary<string, object?>
            {
                ["graph_id"] = graphId,
                ["deleted_count"] = objects.Count,
                ["cleared"] = true,
                ["recomputed"] = recompute,
            };
        });
    }

    private static object[] ConnectionsForGraph(
        GH_Document document,
        IReadOnlyCollection<IGH_DocumentObject> objects
    )
    {
        var ids = objects.Select(item => item.InstanceGuid).ToHashSet();
        return GrasshopperDocumentCommands.Connections(document)
            .OfType<Dictionary<string, object?>>()
            .Where(connection => connection.TryGetValue("source_instance_id", out var source)
                && connection.TryGetValue("target_instance_id", out var target)
                && Guid.TryParse(source as string, out var sourceId)
                && Guid.TryParse(target as string, out var targetId)
                && ids.Contains(sourceId)
                && ids.Contains(targetId))
            .Cast<object>()
            .ToArray();
    }
}
