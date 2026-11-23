using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperBuild
{
    [McpCommand("gh_build_graph", ReadOnly = false, SupportsDryRun = false)]
    internal static object BuildGraph(RequestContext context, JsonElement parameters)
    {
        var total = Stopwatch.StartNew();
        var graphId = NativeFunctions.Has(parameters, "graph_id")
            ? GrasshopperHelpers.RequireGraphId(parameters)
            : "MCPGraph_" + DateTime.UtcNow.ToString("yyyyMMddHHmmssfff", CultureInfo.InvariantCulture);
        var componentSpecs = NativeFunctions.Property(parameters, "components");
        if (componentSpecs.ValueKind != JsonValueKind.Array || componentSpecs.GetArrayLength() == 0)
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper graph requires at least one component.");
        }

        var openCanvas = NativeFunctions.Boolean(parameters, "open_canvas", true);
        var (selected, canvas, _) = GrasshopperDocumentCommands.EnsureDocument(
            context, openCanvas, makeActive: true, newIfMissing: true);
        var document = (context with { GrasshopperDocument = selected }).RequireGrasshopperDocument();
        context = context with { GrasshopperDocument = document, GrasshopperCanvas = canvas };
        var rollbackOnError = NativeFunctions.Boolean(parameters, "rollback_on_error", true);
        return GrasshopperOperations.Mutate(
            document,
            "Build MCP Grasshopper graph",
            () =>
            {
                var mutation = Stopwatch.StartNew();
                var aliases = new Dictionary<string, Grasshopper.Kernel.IGH_DocumentObject>(StringComparer.OrdinalIgnoreCase);
                var created = new List<Grasshopper.Kernel.IGH_DocumentObject>();
                foreach (var componentSpec in componentSpecs.EnumerateArray())
                {
                    var item = GrasshopperGraphHelpers.CreateComponent(document, componentSpec, aliases, graphId);
                    created.Add(item);
                    StoreRole(document, graphId, componentSpec, item);
                }

                var valueCount = 0;
                foreach (var value in Elements(parameters, "values"))
                {
                    GrasshopperGraphHelpers.SetValue(document, value, graphId, aliases);
                    valueCount++;
                }
                var connectionCount = 0;
                foreach (var connection in Elements(parameters, "connections"))
                {
                    GrasshopperGraphHelpers.Connect(document, connection, graphId, aliases);
                    connectionCount++;
                }

                ApplyPreviewUpdates(document, NativeFunctions.Property(parameters, "preview_updates"), aliases);
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
                mutation.Stop();
                var solve = new Stopwatch();
                if (recompute)
                {
                    foreach (var item in created)
                        item.ExpireSolution(true);
                    solve.Start();
                    GrasshopperOperations.Recompute(document, expireAll: false);
                    solve.Stop();
                }
                var pivot = created.Last().Attributes.Pivot;
                GrasshopperComponents.Redraw(context, document, new PointF(pivot.X, pivot.Y));
                var summary = GrasshopperReadback.GraphSummary(created, graphId,
                    aliases.ToDictionary(entry => entry.Value.InstanceGuid, entry => entry.Key));

                return new Dictionary<string, object?>
                {
                    ["graph_id"] = graphId,
                    ["success"] = true,
                    ["aliases"] = aliases.ToDictionary(
                        entry => entry.Key,
                        entry => entry.Value.InstanceGuid.ToString("D"),
                        StringComparer.Ordinal
                    ),
                    ["components"] = created.Select(item => new Dictionary<string, object?>
                    {
                        ["instance_id"] = item.InstanceGuid.ToString("D"),
                        ["name"] = item.Name,
                        ["nickname"] = item.NickName,
                        ["category"] = item.Category,
                        ["subcategory"] = item.SubCategory,
                        ["position"] = GrasshopperComponents.Position(item),
                    }).ToArray(),
                    ["preview_policy"] = previewPolicyResults,
                    ["groups"] = groups,
                    ["layout"] = layout,
                    ["component_count"] = created.Count,
                    ["created_component_count"] = created.Count,
                    ["created_group_count"] = groups.Length,
                    ["connection_count"] = connectionCount,
                    ["value_count"] = valueCount,
                    ["recomputed"] = recompute,
                    ["rolled_back"] = false,
                    ["mutation_duration_ms"] = mutation.ElapsedMilliseconds,
                    ["solution_duration_ms"] = solve.ElapsedMilliseconds,
                    ["verification_duration_ms"] = 0,
                    ["duration_ms"] = total.ElapsedMilliseconds,
                    ["diagnostics"] = GrasshopperSolution.SolutionSummary(document),
                    ["visibility"] = GrasshopperDocumentCommands.Visibility(context, document, canvas),
                    ["summary"] = summary,
                    ["message"] = $"Built Grasshopper graph with {created.Count} component(s) and {connectionCount} connection(s)",
                };
            },
            rollbackOnError
        );
    }

    private static void ApplyPreviewUpdates(
        Grasshopper.Kernel.GH_Document document,
        JsonElement updates,
        IReadOnlyDictionary<string, Grasshopper.Kernel.IGH_DocumentObject> aliases
    )
    {
        if (!NativeFunctions.Has(updates, "enabled"))
        {
            return;
        }

        var enabled = NativeFunctions.Boolean(updates, "enabled");
        foreach (var item in aliases.Values.GroupBy(item => item.InstanceGuid).Select(group => group.First()))
        {
            if (item is not Grasshopper.Kernel.IGH_PreviewObject preview || preview.Hidden == !enabled)
            {
                continue;
            }

            GrasshopperOperations.RecordChange(document, item, "Update MCP Grasshopper preview visibility");
            preview.Hidden = !enabled;
        }
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
