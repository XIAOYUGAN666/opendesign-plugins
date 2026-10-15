using System.Text.Json;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperComponents
{
    [McpCommand("gh_list_components", ReadOnly = true, SupportsDryRun = false)]
    internal static object ListComponents(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var category = NativeFunctions.Text(parameters, "category");
        var name = NativeFunctions.Text(parameters, "name");
        var limit = Math.Max(1, Math.Min(1000, NativeFunctions.Integer(parameters, "limit", 100)));
        var matches = document.Objects
            .Where(item => item is not GH_Group)
            .Where(item => string.IsNullOrWhiteSpace(category)
                || string.Equals(item.Category, category, StringComparison.OrdinalIgnoreCase))
            .Where(item => string.IsNullOrWhiteSpace(name)
                || item.Name.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0
                || item.NickName.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
            .ToArray();
        var selected = matches.Take(limit).ToArray();
        return new Dictionary<string, object?>
        {
            ["components"] = selected.Select(item => GrasshopperHelpers.ObjectInfo(item)).ToArray(),
            ["count"] = selected.Length,
            ["total_count"] = matches.Length,
            ["truncated"] = matches.Length > selected.Length,
        };
    }

    [McpCommand("gh_get_component_info", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetComponentInfo(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var item = GrasshopperHelpers.RequireObject(document, parameters);
        return GrasshopperHelpers.ObjectInfo(item, includeValues: true, maxItems: 100);
    }

    [McpCommand("gh_add_component", ReadOnly = false, SupportsDryRun = false)]
    internal static object AddComponent(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        return GrasshopperOperations.Mutate(document, "Add MCP Grasshopper component", () =>
        {
            var name = NativeFunctions.Text(parameters, "component_name");
            var guid = NativeFunctions.Text(parameters, "component_guid")
                ?? NativeFunctions.Text(parameters, "guid");
            var proxy = GrasshopperHelpers.RequireProxy(name, guid);
            var item = GrasshopperHelpers.CreateInstance(proxy);
            GrasshopperOperations.ApplyObjectOptions(item, parameters, includeValue: true);
            GrasshopperOperations.AddObject(document, item, "Add MCP Grasshopper component");
            return new Dictionary<string, object?>
            {
                ["component"] = GrasshopperHelpers.ObjectInfo(item, includeValues: true, maxItems: 100),
                ["added"] = true,
            };
        });
    }

    [McpCommand("gh_layout_components", ReadOnly = false, SupportsDryRun = false)]
    internal static object LayoutComponents(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var selectedIds = NativeFunctions.Has(parameters, "component_ids")
            ? NativeFunctions.Ids(parameters, "component_ids").ToHashSet()
            : null;
        var includeGroups = NativeFunctions.Boolean(parameters, "include_groups");
        var objects = document.Objects
            .Where(item => selectedIds is null || selectedIds.Contains(item.InstanceGuid))
            .Where(item => includeGroups || item is not GH_Group)
            .ToArray();
        if (selectedIds is not null && selectedIds.Count != objects.Length)
        {
            throw new NativeCommandException("componentNotFound", "One or more Grasshopper objects were not found.");
        }

        var start = ReadPosition(parameters, "start_position", 40, 40);
        var xSpacing = ReadSpacing(parameters, "x_spacing", 220);
        var ySpacing = ReadSpacing(parameters, "y_spacing", 90);
        var positions = Layout(document, objects, start, xSpacing, ySpacing, maxColumns: 100);
        var recompute = NativeFunctions.Boolean(parameters, "recompute");
        return GrasshopperOperations.Mutate(document, "Layout MCP Grasshopper components", () =>
        {
            foreach (var item in objects)
            {
                GrasshopperOperations.RecordChange(document, item, "Layout MCP Grasshopper components");
                GrasshopperOperations.SetPosition(item, JsonSerializer.SerializeToElement(positions[item.InstanceGuid]));
            }

            if (recompute)
            {
                GrasshopperOperations.Recompute(document, expireAll: false);
            }

            return new Dictionary<string, object?>
            {
                ["laid_out_count"] = objects.Length,
                ["positions"] = positions.ToDictionary(
                    entry => entry.Key.ToString("D"),
                    entry => (object)entry.Value
                ),
            };
        });
    }

    [McpCommand("gh_delete_component", ReadOnly = false, SupportsDryRun = false)]
    internal static object DeleteComponent(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var item = GrasshopperHelpers.RequireObject(document, parameters);
        var id = item.InstanceGuid;
        return GrasshopperOperations.Mutate(document, "Delete MCP Grasshopper component", () =>
        {
            GrasshopperOperations.RemoveObjects(document, new[] { item }, "Delete MCP Grasshopper component");
            return new Dictionary<string, object?>
            {
                ["instance_id"] = id.ToString("D"),
                ["deleted"] = true,
            };
        });
    }

    [McpCommand("gh_update_component", ReadOnly = false, SupportsDryRun = false)]
    internal static object UpdateComponent(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var item = GrasshopperHelpers.RequireObject(document, parameters);
        if (!NativeFunctions.Has(parameters, "new_nickname")
            && !NativeFunctions.Has(parameters, "position")
            && !NativeFunctions.Has(parameters, "enabled")
            && !NativeFunctions.Has(parameters, "preview"))
        {
            throw new NativeCommandException("invalidParameters", "At least one Grasshopper component update is required.");
        }

        return GrasshopperOperations.Mutate(document, "Update MCP Grasshopper component", () =>
        {
            GrasshopperOperations.RecordChange(document, item, "Update MCP Grasshopper component");
            if (NativeFunctions.Has(parameters, "new_nickname"))
            {
                var nickname = NativeFunctions.Text(parameters, "new_nickname");
                if (nickname is null || nickname.Trim().Length == 0)
                {
                    throw new NativeCommandException("invalidParameters", "The component nickname cannot be empty.");
                }

                item.NickName = nickname.Trim();
            }

            if (NativeFunctions.Has(parameters, "position"))
            {
                GrasshopperOperations.SetPosition(item, NativeFunctions.Property(parameters, "position"));
            }

            if (NativeFunctions.Has(parameters, "enabled"))
            {
                if (item is not IGH_ActiveObject active)
                {
                    throw new NativeCommandException("unsupportedComponentState", "The selected Grasshopper object cannot be enabled or disabled.");
                }

                active.Locked = !NativeFunctions.Boolean(parameters, "enabled");
            }

            if (NativeFunctions.Has(parameters, "preview"))
            {
                if (item is not IGH_PreviewObject preview)
                {
                    throw new NativeCommandException("unsupportedComponentState", "The selected Grasshopper object does not support preview visibility.");
                }

                preview.Hidden = !NativeFunctions.Boolean(parameters, "preview");
            }

            return GrasshopperHelpers.ObjectInfo(item, includeValues: true, maxItems: 100);
        });
    }

    [McpCommand("gh_clear_canvas", ReadOnly = false, SupportsDryRun = false)]
    internal static object ClearCanvas(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var includeGroups = NativeFunctions.Boolean(parameters, "include_groups", true);
        var objects = document.Objects
            .Where(item => includeGroups || item is not GH_Group)
            .ToArray();
        return GrasshopperOperations.Mutate(document, "Clear MCP Grasshopper canvas", () =>
        {
            GrasshopperOperations.RemoveObjects(document, objects, "Clear MCP Grasshopper canvas");
            if (NativeFunctions.Boolean(parameters, "recompute"))
            {
                GrasshopperOperations.Recompute(document, expireAll: true);
            }

            return new Dictionary<string, object?>
            {
                ["deleted_count"] = objects.Length,
                ["cleared"] = true,
            };
        });
    }

    internal static Dictionary<Guid, double[]> Layout(
        GH_Document document,
        IReadOnlyCollection<IGH_DocumentObject> objects,
        double[] start,
        double xSpacing,
        double ySpacing,
        int maxColumns
    )
    {
        var ids = objects.Select(item => item.InstanceGuid).ToHashSet();
        var successors = objects.ToDictionary(item => item.InstanceGuid, _ => new List<Guid>());
        var indegree = objects.ToDictionary(item => item.InstanceGuid, _ => 0);
        foreach (var targetOwner in objects)
        {
            foreach (var input in GrasshopperHelpers.Inputs(targetOwner))
            {
                foreach (var source in input.Sources)
                {
                    var sourceOwner = GrasshopperDocumentCommands.FindOwner(document, source);
                    if (sourceOwner is null || !ids.Contains(sourceOwner.InstanceGuid))
                    {
                        continue;
                    }

                    successors[sourceOwner.InstanceGuid].Add(targetOwner.InstanceGuid);
                    indegree[targetOwner.InstanceGuid]++;
                }
            }
        }

        var depth = objects.ToDictionary(item => item.InstanceGuid, _ => 0);
        var queue = new Queue<Guid>(indegree.Where(entry => entry.Value == 0).Select(entry => entry.Key));
        while (queue.Count > 0)
        {
            var current = queue.Dequeue();
            foreach (var next in successors[current])
            {
                depth[next] = Math.Max(depth[next], depth[current] + 1);
                indegree[next]--;
                if (indegree[next] == 0)
                {
                    queue.Enqueue(next);
                }
            }
        }

        var rowsByDepth = new Dictionary<int, int>();
        var maxRowsPerBand = Math.Max(1, objects.Count);
        var bandHeight = maxRowsPerBand * ySpacing;
        var positions = new Dictionary<Guid, double[]>();
        foreach (var item in objects.OrderBy(item => depth[item.InstanceGuid]).ThenBy(item => item.InstanceGuid))
        {
            var itemDepth = depth[item.InstanceGuid];
            var column = itemDepth % Math.Max(1, maxColumns);
            var band = itemDepth / Math.Max(1, maxColumns);
            var row = rowsByDepth.TryGetValue(itemDepth, out var currentRow) ? currentRow : 0;
            rowsByDepth[itemDepth] = row + 1;
            positions[item.InstanceGuid] = new[]
            {
                start[0] + column * xSpacing,
                start[1] + band * bandHeight + row * ySpacing,
            };
        }

        return positions;
    }

    internal static double[] ReadPosition(JsonElement parameters, string key, double defaultX, double defaultY)
    {
        var value = NativeFunctions.Property(parameters, key);
        if (value.ValueKind == JsonValueKind.Undefined)
        {
            return new[] { defaultX, defaultY };
        }

        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() != 2)
        {
            throw new NativeCommandException("invalidParameters", "A canvas position must contain two coordinates.");
        }

        return value.EnumerateArray().Select(NativeFunctions.ReadFiniteNumber).ToArray();
    }

    internal static double ReadSpacing(JsonElement parameters, string key, double fallback)
    {
        var value = NativeFunctions.Number(parameters, key, fallback);
        if (double.IsNaN(value) || double.IsInfinity(value) || value <= 0)
        {
            throw new NativeCommandException("invalidParameters", "Grasshopper layout spacing must be positive and finite.");
        }

        return value;
    }
}
