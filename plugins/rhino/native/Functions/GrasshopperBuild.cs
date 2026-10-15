using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperBuild
{
    [McpCommand("gh_build_graph", ReadOnly = false, SupportsDryRun = false)]
    internal static object BuildGraph(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var graphId = NativeFunctions.Has(parameters, "graph_id")
            ? GrasshopperHelpers.RequireGraphId(parameters)
            : null;
        var componentSpecs = NativeFunctions.Property(parameters, "components");
        if (componentSpecs.ValueKind != JsonValueKind.Array || componentSpecs.GetArrayLength() == 0)
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper graph requires at least one component.");
        }

        var rollbackOnError = NativeFunctions.Boolean(parameters, "rollback_on_error", true);
        return GrasshopperOperations.Mutate(
            document,
            "Build MCP Grasshopper graph",
            () =>
            {
                var canvasOpened = GrasshopperGraphHelpers.OpenCanvas(
                    context,
                    document,
                    NativeFunctions.Boolean(parameters, "open_canvas", true)
                );
                var aliases = new Dictionary<string, Grasshopper.Kernel.IGH_DocumentObject>(StringComparer.Ordinal);
                var created = new List<Grasshopper.Kernel.IGH_DocumentObject>();
                foreach (var componentSpec in componentSpecs.EnumerateArray())
                {
                    var item = GrasshopperGraphHelpers.CreateComponent(document, componentSpec, aliases, graphId);
                    created.Add(item);
                    StoreRole(document, graphId, componentSpec, item);
                }

                var connectionResults = new List<object>();
                foreach (var connection in Elements(parameters, "connections"))
                {
                    connectionResults.Add(GrasshopperGraphHelpers.Connect(document, connection, graphId, aliases));
                }

                var valueResults = new List<object?>();
                foreach (var value in Elements(parameters, "values"))
                {
                    valueResults.Add(GrasshopperGraphHelpers.SetValue(document, value, graphId, aliases));
                }

                var previewResults = ApplyPreviewUpdates(document, NativeFunctions.Property(parameters, "preview_updates"), aliases);
                var previewPolicyResults = GrasshopperGraphHelpers.ApplyPreviewPolicy(
                    document,
                    NativeFunctions.Property(parameters, "preview_policy"),
                    aliases,
                    graphId
                );
                var groups = GrasshopperGraphHelpers.ApplyGroups(
                    document,
                    NativeFunctions.Property(parameters, "groups"),
                    aliases,
                    graphId
                );
                var layout = GrasshopperGraphHelpers.ApplyLayout(
                    document,
                    NativeFunctions.Property(parameters, "layout"),
                    aliases,
                    graphId
                );
                var recompute = NativeFunctions.Boolean(parameters, "recompute", true);
                if (recompute)
                {
                    GrasshopperOperations.Recompute(document, expireAll: true);
                }

                return new Dictionary<string, object?>
                {
                    ["graph_id"] = graphId,
                    ["aliases"] = aliases.ToDictionary(
                        entry => entry.Key,
                        entry => entry.Value.InstanceGuid.ToString("D"),
                        StringComparer.Ordinal
                    ),
                    ["components"] = created.Select(item => GrasshopperHelpers.ObjectInfo(item, includeValues: true, maxItems: 100)).ToArray(),
                    ["connections"] = connectionResults,
                    ["values"] = valueResults,
                    ["preview_updates"] = previewResults,
                    ["preview_policy"] = previewPolicyResults,
                    ["groups"] = groups,
                    ["layout"] = layout,
                    ["component_count"] = created.Count,
                    ["connection_count"] = connectionResults.Count,
                    ["recomputed"] = recompute,
                    ["canvas_opened"] = canvasOpened,
                    ["solution"] = recompute ? GrasshopperSolution.SolutionSummary(document) : null,
                };
            },
            rollbackOnError
        );
    }

    private static object[] ApplyPreviewUpdates(
        Grasshopper.Kernel.GH_Document document,
        JsonElement updates,
        IReadOnlyDictionary<string, Grasshopper.Kernel.IGH_DocumentObject> aliases
    )
    {
        if (!NativeFunctions.Has(updates, "enabled"))
        {
            return Array.Empty<object>();
        }

        var enabled = NativeFunctions.Boolean(updates, "enabled");
        var results = new List<object>();
        foreach (var item in aliases.Values.GroupBy(item => item.InstanceGuid).Select(group => group.First()))
        {
            if (item is not Grasshopper.Kernel.IGH_PreviewObject preview || preview.Hidden == !enabled)
            {
                continue;
            }

            GrasshopperOperations.RecordChange(document, item, "Update MCP Grasshopper preview visibility");
            preview.Hidden = !enabled;
            results.Add(new Dictionary<string, object?>
            {
                ["instance_id"] = item.InstanceGuid.ToString("D"),
                ["preview"] = enabled,
            });
        }

        return results.ToArray();
    }

    private static IEnumerable<JsonElement> Elements(JsonElement parameters, string key)
    {
        var value = NativeFunctions.Property(parameters, key);
        return value.ValueKind == JsonValueKind.Array
            ? value.EnumerateArray().ToArray()
            : Array.Empty<JsonElement>();
    }

    private static void StoreRole(
        Grasshopper.Kernel.GH_Document document,
        string? graphId,
        JsonElement componentSpec,
        Grasshopper.Kernel.IGH_DocumentObject item
    )
    {
        var role = NativeFunctions.Text(componentSpec, "role");
        var alias = NativeFunctions.Text(componentSpec, "alias");
        if (role is not null && role.Length > 0
            && graphId is not null && graphId.Length > 0
            && alias is not null && alias.Length > 0)
        {
            GrasshopperHelpers.StoreGraphRole(document, graphId, alias, role);
        }
    }
}
