using System.Drawing;
using System.Text.Json;
using Grasshopper;
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
        var components = document.Objects.OfType<IGH_Component>()
            .Where(item => string.IsNullOrWhiteSpace(category)
                || string.Equals(item.Category, category, StringComparison.OrdinalIgnoreCase))
            .Where(item => string.IsNullOrWhiteSpace(name)
                || item.Name.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0
                || item.NickName.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
            .Take(limit)
            .Select(item => new Dictionary<string, object?>
            {
                ["instance_id"] = item.InstanceGuid.ToString("D"),
                ["name"] = item.Name,
                ["nickname"] = item.NickName,
                ["category"] = item.Category,
                ["subcategory"] = item.SubCategory,
                ["description"] = item.Description,
                ["position"] = Position(item),
                ["input_count"] = item.Params.Input.Count,
                ["output_count"] = item.Params.Output.Count,
                ["runtime_message_level"] = item.RuntimeMessageLevel.ToString(),
            })
            .ToArray();
        return new Dictionary<string, object?>
        {
            ["components"] = components,
            ["count"] = components.Length,
        };
    }

    [McpCommand("gh_get_component_info", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetComponentInfo(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var item = GrasshopperHelpers.RequireObject(document, parameters);
        return GrasshopperReadback.DetailedComponent(item);
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
            if (!NativeFunctions.Has(parameters, "position"))
            {
                GrasshopperOperations.SetPosition(item, JsonSerializer.SerializeToElement(NextPosition(document)));
            }
            GrasshopperOperations.AddObject(document, item, "Add MCP Grasshopper component");
            GrasshopperOperations.Recompute(document, expireAll: false);
            Redraw(context, document, item.Attributes.Pivot);
            return new Dictionary<string, object?>
            {
                ["instance_id"] = item.InstanceGuid.ToString("D"),
                ["name"] = item.Name,
                ["nickname"] = item.NickName,
                ["category"] = item.Category,
                ["subcategory"] = item.SubCategory,
                ["position"] = Position(item),
                ["message"] = $"Added component '{item.Name}' to canvas",
            };
        });
    }

    [McpCommand("gh_layout_components", ReadOnly = false, SupportsDryRun = false)]
    internal static object LayoutComponents(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var selectedIds = NativeFunctions.Has(parameters, "component_ids")
            ? NativeFunctions.Ids(parameters, "component_ids").Distinct().ToArray()
            : Array.Empty<Guid>();
        var includeGroups = NativeFunctions.Boolean(parameters, "include_groups");
        var selected = selectedIds.Length == 0
            ? document.Objects.AsEnumerable()
            : selectedIds.Select(id => document.FindObject(id, true)
                ?? throw new NativeCommandException("componentNotFound", "A Grasshopper object to layout was not found."));
        var objects = selected
            .Where(item => includeGroups || item is not GH_Group)
            .ToArray();
        if (objects.Length == 0)
        {
            return new Dictionary<string, object?>
            {
                ["layout_count"] = 0,
                ["edge_count"] = 0,
                ["positions"] = Array.Empty<object>(),
                ["recomputed"] = false,
                ["message"] = "No Grasshopper objects to layout",
            };
        }

        var start = ReadPosition(parameters, "start_position", 40, 40);
        var xSpacing = Math.Max(60, ReadSpacing(parameters, "x_spacing", 220));
        var ySpacing = Math.Max(40, ReadSpacing(parameters, "y_spacing", 90));
        var (positions, edgeCount) = Layout(objects, start, xSpacing, ySpacing, maxColumns: 0);
        var recompute = NativeFunctions.Boolean(parameters, "recompute");
        return GrasshopperOperations.Mutate(document, "Layout MCP Grasshopper components", () =>
        {
            foreach (var item in objects)
            {
                GrasshopperOperations.RecordChange(document, item, "Layout MCP Grasshopper components");
                GrasshopperOperations.SetPosition(item, JsonSerializer.SerializeToElement(positions[item.InstanceGuid]));
                item.ExpireSolution(false);
            }

            if (recompute)
            {
                GrasshopperOperations.Recompute(document, expireAll: false);
            }

            Redraw(context, document, new PointF((float)start[0], (float)start[1]));

            return new Dictionary<string, object?>
            {
                ["layout_count"] = objects.Length,
                ["edge_count"] = edgeCount,
                ["positions"] = objects.Select(item => new Dictionary<string, object?>
                {
                    ["instance_id"] = item.InstanceGuid.ToString("D"),
                    ["name"] = item.Name,
                    ["nickname"] = item.NickName,
                    ["position"] = positions[item.InstanceGuid],
                }).ToArray(),
                ["recomputed"] = recompute,
                ["message"] = $"Laid out {objects.Length} Grasshopper object(s)",
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
            GrasshopperOperations.Recompute(document, expireAll: false);
            return new Dictionary<string, object?>
            {
                ["deleted_id"] = id.ToString("D"),
                ["name"] = item.Name,
                ["nickname"] = item.NickName,
                ["message"] = $"Deleted component '{item.NickName}' ({item.Name})",
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

            if (NativeFunctions.Has(parameters, "preview") && item is IGH_PreviewObject preview)
                preview.Hidden = !NativeFunctions.Boolean(parameters, "preview");

            item.ExpireSolution(false);
            GrasshopperOperations.Recompute(document, expireAll: false);
            Redraw(context, document);
            return new Dictionary<string, object?>
            {
                ["instance_id"] = item.InstanceGuid.ToString("D"),
                ["name"] = item.Name,
                ["nickname"] = item.NickName,
                ["position"] = Position(item),
                ["message"] = $"Updated component '{item.NickName}'",
            };
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
            var recompute = NativeFunctions.Boolean(parameters, "recompute");
            if (recompute)
            {
                GrasshopperOperations.Recompute(document, expireAll: false);
            }

            return new Dictionary<string, object?>
            {
                ["deleted_count"] = objects.Length,
                ["include_groups"] = includeGroups,
                ["recomputed"] = recompute,
                ["message"] = $"Cleared {objects.Length} Grasshopper canvas object(s)",
            };
        });
    }

    internal static double[] Position(IGH_DocumentObject item) =>
        new double[] { item.Attributes?.Pivot.X ?? 0, item.Attributes?.Pivot.Y ?? 0 };

    internal static double[] NextPosition(GH_Document document)
    {
        var pivots = document.Objects.Where(item => item.Attributes is not null)
            .Select(item => item.Attributes.Pivot).ToArray();
        return pivots.Length == 0
            ? new[] { 40d, 40d }
            : new[] { pivots.Max(pivot => pivot.X) + 220d, pivots.Min(pivot => pivot.Y) };
    }

    internal static void Redraw(RequestContext context, GH_Document document, PointF? focus = null)
    {
        if (context.GrasshopperCanvas is not { } canvas || !ReferenceEquals(canvas.Document, document))
            return;
        if (focus is { } center)
            canvas.Viewport.Focus(center);
        Instances.InvalidateCanvas();
        Instances.RedrawCanvas();
    }

    internal static (Dictionary<Guid, double[]> Positions, int EdgeCount) Layout(
        IReadOnlyCollection<IGH_DocumentObject> objects,
        double[] start,
        double xSpacing,
        double ySpacing,
        int maxColumns
    )
    {
        var objectSet = objects.ToHashSet();
        var incoming = objects.ToDictionary(item => item, _ => new HashSet<IGH_DocumentObject>());
        foreach (var targetOwner in objects)
        {
            foreach (var input in GrasshopperHelpers.Inputs(targetOwner))
            {
                foreach (var source in input.Sources)
                {
                    var sourceOwner = source.Attributes?.GetTopLevel.DocObject;
                    if (sourceOwner is not null && sourceOwner != targetOwner && objectSet.Contains(sourceOwner))
                        incoming[targetOwner].Add(sourceOwner);
                }
            }
        }

        var positions = new Dictionary<Guid, double[]>();
        var ordered = objects.OrderBy(item => item.Attributes?.Pivot.X ?? 0)
            .ThenBy(item => item.Attributes?.Pivot.Y ?? 0)
            .ThenBy(item => item.NickName)
            .ToArray();
        var edgeCount = incoming.Values.Sum(sources => sources.Count);
        if (edgeCount == 0)
        {
            var rows = maxColumns > 0
                ? Math.Max(1, (int)Math.Ceiling((double)ordered.Length / maxColumns))
                : Math.Max(1, (int)Math.Ceiling(Math.Sqrt(ordered.Length)));
            for (var i = 0; i < ordered.Length; i++)
            {
                var column = maxColumns > 0 ? i % maxColumns : i / rows;
                var row = maxColumns > 0 ? i / maxColumns : i % rows;
                positions[ordered[i].InstanceGuid] = new[] { start[0] + column * xSpacing, start[1] + row * ySpacing };
            }
            return (positions, edgeCount);
        }

        var memo = new Dictionary<IGH_DocumentObject, int>();
        var visiting = new HashSet<IGH_DocumentObject>();
        int LevelOf(IGH_DocumentObject item)
        {
            if (memo.TryGetValue(item, out var level))
                return level;
            if (!visiting.Add(item))
                return 0;
            level = 0;
            foreach (var source in incoming[item])
                level = Math.Max(level, LevelOf(source) + 1);
            visiting.Remove(item);
            memo[item] = level;
            return level;
        }

        var levels = ordered.GroupBy(LevelOf).OrderBy(group => group.Key).ToArray();
        var bandOffsets = new Dictionary<int, int>();
        if (maxColumns > 0)
        {
            var nextOffset = 0;
            foreach (var band in levels.GroupBy(level => level.Key / maxColumns))
            {
                bandOffsets[band.Key] = nextOffset;
                nextOffset += band.Max(level => level.Count()) + 1;
            }
        }
        foreach (var level in levels)
        {
            var column = maxColumns > 0 ? level.Key % maxColumns : level.Key;
            var row = maxColumns > 0 ? bandOffsets[level.Key / maxColumns] : 0;
            foreach (var item in level)
            {
                positions[item.InstanceGuid] = new[] { start[0] + column * xSpacing, start[1] + row * ySpacing };
                row++;
            }
        }
        return (positions, edgeCount);
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
