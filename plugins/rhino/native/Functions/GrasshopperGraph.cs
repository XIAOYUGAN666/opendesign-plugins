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
        var byId = aliases.GroupBy(entry => entry.Value.InstanceGuid)
            .ToDictionary(group => group.Key, group => group.First().Key);
        var objects = aliases.Values.Concat(GrasshopperHelpers.FindGraphGroups(document, graphId))
            .Distinct().ToArray();
        var result = GrasshopperReadback.CanvasObjects(
            objects, includeConnections: true, includeValues, maxItems, byId, graphId);
        result["graph_id"] = graphId;
        result["object_count"] = objects.Length;
        result["summary"] = GrasshopperReadback.GraphSummary(objects.Where(item => item is not GH_Group), graphId, byId);
        return result;
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

}
