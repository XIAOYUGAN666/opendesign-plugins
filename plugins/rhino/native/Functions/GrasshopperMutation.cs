using System.Text.Json;
using Grasshopper.Kernel;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperMutation
{
    [McpCommand("gh_mutate_graph", ReadOnly = false, SupportsDryRun = false)]
    internal static object MutateGraph(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var graphId = NativeFunctions.Has(parameters, "graph_id")
            ? GrasshopperHelpers.RequireGraphId(parameters)
            : null;
        var operations = NativeFunctions.Property(parameters, "operations");
        if (operations.ValueKind != JsonValueKind.Array || operations.GetArrayLength() == 0)
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper graph mutation requires at least one operation.");
        }

        var rollbackOnError = NativeFunctions.Boolean(parameters, "rollback_on_error", true);
        return GrasshopperOperations.Mutate(
            document,
            "Mutate MCP Grasshopper graph",
            () =>
            {
                var canvasOpened = GrasshopperGraphHelpers.OpenCanvas(
                    context,
                    document,
                    NativeFunctions.Boolean(parameters, "open_canvas", true)
                );
                var aliases = graphId is null
                    ? new Dictionary<string, IGH_DocumentObject>(StringComparer.Ordinal)
                    : GrasshopperHelpers.GraphAliases(document, graphId)
                        .ToDictionary(entry => entry.Key, entry => entry.Value, StringComparer.Ordinal);
                var results = new List<object?>();
                foreach (var operation in operations.EnumerateArray())
                {
                    results.Add(ApplyOperation(document, operation, graphId, aliases));
                }

                var previewPolicy = GrasshopperGraphHelpers.ApplyPreviewPolicy(
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
                    GrasshopperOperations.Recompute(document, expireAll: false);
                }

                var verification = GrasshopperGraphHelpers.Verify(
                    document,
                    NativeFunctions.Property(parameters, "verify"),
                    graphId,
                    aliases
                );
                GrasshopperGraphHelpers.FailOnVerificationError(parameters, verification);
                return new Dictionary<string, object?>
                {
                    ["graph_id"] = graphId,
                    ["operations"] = results,
                    ["operation_count"] = results.Count,
                    ["aliases"] = aliases.ToDictionary(
                        entry => entry.Key,
                        entry => entry.Value.InstanceGuid.ToString("D"),
                        StringComparer.Ordinal
                    ),
                    ["preview_policy"] = previewPolicy,
                    ["groups"] = groups,
                    ["layout"] = layout,
                    ["recomputed"] = recompute,
                    ["canvas_opened"] = canvasOpened,
                    ["verification"] = verification,
                    ["solution"] = GrasshopperSolution.SolutionSummary(document),
                };
            },
            rollbackOnError
        );
    }

    private static object? ApplyOperation(
        GH_Document document,
        JsonElement operation,
        string? defaultGraphId,
        Dictionary<string, IGH_DocumentObject> aliases
    )
    {
        var op = NativeFunctions.Text(operation, "op");
        var graphId = NativeFunctions.Has(operation, "graph_id")
            ? GrasshopperHelpers.RequireGraphId(operation)
            : defaultGraphId;
        switch (op)
        {
            case "create":
            {
                var item = GrasshopperGraphHelpers.CreateComponent(document, operation, aliases, graphId);
                StoreRole(document, graphId, operation);
                return new Dictionary<string, object?>
                {
                    ["op"] = op,
                    ["alias"] = NativeFunctions.Text(operation, "alias"),
                    ["object"] = GrasshopperHelpers.ObjectInfo(item, includeValues: true, maxItems: 100),
                };
            }
            case "connect":
                return new Dictionary<string, object?>
                {
                    ["op"] = op,
                    ["connection"] = GrasshopperGraphHelpers.Connect(document, operation, graphId, aliases),
                };
            case "disconnect":
                return new Dictionary<string, object?>
                {
                    ["op"] = op,
                    ["connection"] = GrasshopperGraphHelpers.Disconnect(document, operation, graphId, aliases),
                };
            case "set":
            {
                var value = GrasshopperGraphHelpers.SetValue(document, operation, graphId, aliases);
                return new Dictionary<string, object?>
                {
                    ["op"] = op,
                    ["target"] = NativeFunctions.Text(operation, "target"),
                    ["value"] = value,
                };
            }
            case "update":
                return UpdateObject(document, operation, graphId, aliases);
            case "delete":
            {
                var target = GrasshopperGraphHelpers.ResolveSelector(document, operation, "target", graphId, aliases);
                var id = target.InstanceGuid;
                GrasshopperOperations.RemoveObjects(document, new[] { target }, "Delete MCP Grasshopper graph object");
                foreach (var key in aliases.Where(entry => entry.Value.InstanceGuid == id).Select(entry => entry.Key).ToArray())
                {
                    aliases.Remove(key);
                }

                return new Dictionary<string, object?>
                {
                    ["op"] = op,
                    ["instance_id"] = id.ToString("D"),
                    ["deleted"] = true,
                };
            }
            case "recompute":
                GrasshopperOperations.Recompute(document, expireAll: false);
                return new Dictionary<string, object?>
                {
                    ["op"] = op,
                    ["solution"] = GrasshopperSolution.SolutionSummary(document),
                };
            default:
                throw new NativeCommandException("invalidParameters", "The Grasshopper graph operation is not supported.");
        }
    }

    private static object UpdateObject(
        GH_Document document,
        JsonElement operation,
        string? graphId,
        Dictionary<string, IGH_DocumentObject> aliases
    )
    {
        var target = GrasshopperGraphHelpers.ResolveSelector(document, operation, "target", graphId, aliases);
        var newNickname = NativeFunctions.Text(operation, "new_nickname")
            ?? NativeFunctions.Text(operation, "nickname");
        var hasPosition = NativeFunctions.Has(operation, "position");
        var hasEnabled = NativeFunctions.Has(operation, "enabled");
        var hasPreview = NativeFunctions.Has(operation, "preview");
        var hasValue = NativeFunctions.Has(operation, "value");
        if (newNickname is not null || hasPosition || hasEnabled || hasPreview)
        {
            GrasshopperOperations.RecordChange(document, target, "Update MCP Grasshopper graph object");
        }

        if (newNickname is not null)
        {
            if (newNickname.Trim().Length == 0)
            {
                throw new NativeCommandException("invalidParameters", "The component nickname cannot be empty.");
            }

            target.NickName = newNickname.Trim();
        }

        if (hasPosition)
        {
            GrasshopperOperations.SetPosition(target, NativeFunctions.Property(operation, "position"));
        }

        if (hasEnabled)
        {
            if (target is not IGH_ActiveObject active)
            {
                throw new NativeCommandException("unsupportedComponentState", "The selected Grasshopper object cannot be enabled or disabled.");
            }

            active.Locked = !NativeFunctions.Boolean(operation, "enabled");
        }

        if (hasPreview)
        {
            if (target is not IGH_PreviewObject preview)
            {
                throw new NativeCommandException("unsupportedComponentState", "The selected Grasshopper object does not support preview visibility.");
            }

            preview.Hidden = !NativeFunctions.Boolean(operation, "preview");
        }

        object? value = null;
        if (hasValue)
        {
            value = GrasshopperOperations.SetValue(
                target,
                NativeFunctions.Property(operation, "value"),
                operation,
                document
            );
        }

        var alias = NativeFunctions.Text(operation, "alias");
        if (alias is not null && alias.Trim().Length > 0)
        {
            aliases[alias] = target;
            if (graphId is not null && graphId.Length > 0)
            {
                GrasshopperHelpers.StoreAlias(document, graphId, alias, target.InstanceGuid);
            }
        }

        StoreRole(document, graphId, operation);
        return new Dictionary<string, object?>
        {
            ["op"] = "update",
            ["instance_id"] = target.InstanceGuid.ToString("D"),
            ["value"] = value,
            ["object"] = GrasshopperHelpers.ObjectInfo(target, includeValues: true, maxItems: 100),
        };
    }

    private static void StoreRole(GH_Document document, string? graphId, JsonElement operation)
    {
        var role = NativeFunctions.Text(operation, "role");
        var alias = NativeFunctions.Text(operation, "alias");
        if (role is not null && role.Length > 0
            && alias is not null && alias.Length > 0
            && graphId is not null && graphId.Length > 0)
        {
            GrasshopperHelpers.StoreGraphRole(document, graphId, alias, role);
        }
    }
}
