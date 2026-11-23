using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;
using Grasshopper.Kernel.Types;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperReadback
{
    internal static Dictionary<string, object?> CanvasObjects(
        IEnumerable<IGH_DocumentObject> objects,
        bool includeConnections,
        bool includeValues,
        int maxItems,
        IReadOnlyDictionary<Guid, string>? aliases = null,
        string? graphId = null)
    {
        var components = new List<Dictionary<string, object?>>();
        var standalone = new List<Dictionary<string, object?>>();
        var groups = new List<Dictionary<string, object?>>();
        foreach (var item in objects)
        {
            var info = GrasshopperHelpers.ObjectInfo(item);
            if (graphId is not null)
            {
                info["graph_id"] = graphId;
                if (aliases is not null && aliases.TryGetValue(item.InstanceGuid, out var alias))
                    info["alias"] = alias;
            }

            if (item is IGH_Param parameter)
            {
                info["type"] = GrasshopperHelpers.SafeParamTypeName(parameter);
                info["source_count"] = parameter.SourceCount;
                info["recipient_count"] = parameter.Recipients.Count;
                if (includeValues)
                    info["value_data"] = ParameterData(parameter, maxItems);
                standalone.Add(info);
            }
            else if (item is GH_Group group)
            {
                info["object_count"] = group.ObjectIDs.Count;
                groups.Add(info);
                continue;
            }
            else if (item is IGH_Component)
            {
                components.Add(info);
            }
            else
            {
                continue;
            }

            info["inputs"] = GrasshopperHelpers.Inputs(item)
                .Select((port, index) => Port(port, index, includeConnections, includeValues, maxItems))
                .ToArray();
            info["outputs"] = GrasshopperHelpers.Outputs(item)
                .Select((port, index) => Port(port, index, includeConnections, includeValues, maxItems))
                .ToArray();
            if (item is IGH_ActiveObject active)
                info["runtime_message_level"] = active.RuntimeMessageLevel.ToString();
            AddSpecialState(item, info);
        }

        return new Dictionary<string, object?>
        {
            ["components"] = components,
            ["component_count"] = components.Count,
            ["standalone_parameters"] = standalone,
            ["standalone_parameter_count"] = standalone.Count,
            ["groups"] = groups,
            ["group_count"] = groups.Count,
        };
    }

    private static void AddSpecialState(IGH_DocumentObject item, Dictionary<string, object?> info)
    {
        switch (item)
        {
            case GH_NumberSlider slider:
                info["special_type"] = "number_slider";
                info["value"] = (double)slider.CurrentValue;
                info["min"] = (double)slider.Slider.Minimum;
                info["max"] = (double)slider.Slider.Maximum;
                info["decimals"] = slider.Slider.DecimalPlaces;
                info["expression"] = slider.Expression;
                break;
            case GH_BooleanToggle toggle:
                info["special_type"] = "boolean_toggle";
                info["value"] = toggle.Value;
                break;
            case GH_Panel panel:
                info["special_type"] = "panel";
                info["content"] = panel.UserText;
                break;
            case GH_ValueList list:
                info["special_type"] = "value_list";
                info["list_mode"] = list.ListMode.ToString();
                info["items"] = list.ListItems.Select(entry => new Dictionary<string, object?>
                {
                    ["name"] = entry.Name,
                    ["expression"] = entry.Expression,
                    ["selected"] = entry.Selected,
                }).ToArray();
                break;
        }
    }

    private static Dictionary<string, object?> Port(
        IGH_Param parameter, int index, bool includeConnections, bool includeValues, int maxItems)
    {
        var info = GrasshopperHelpers.PortInfo(parameter, index);
        info["description"] = parameter.Description;
        info["has_data"] = HasData(parameter);
        info["data_count"] = parameter.VolatileData.DataCount;
        info["branch_count"] = parameter.VolatileData.PathCount;
        if (includeConnections && parameter.SourceCount > 0)
            info["sources"] = parameter.Sources.Select(ConnectionEnd).ToArray();
        if (includeConnections && parameter.Recipients.Count > 0)
            info["recipients"] = parameter.Recipients.Select(ConnectionEnd).ToArray();
        if (includeValues)
            info["value"] = ParameterData(parameter, maxItems);
        return info;
    }

    internal static Dictionary<string, object?> GraphSummary(
        IEnumerable<IGH_DocumentObject> objects,
        string graphId,
        IReadOnlyDictionary<Guid, string> aliases)
    {
        var diagnostics = new List<Dictionary<string, object?>>();
        var allOutputs = new List<IGH_Param>();
        var warningCount = 0;
        var errorCount = 0;
        foreach (var item in objects)
        {
            var info = GrasshopperHelpers.ObjectInfo(item);
            var outputs = GrasshopperHelpers.Outputs(item);
            allOutputs.AddRange(outputs);
            if (item is IGH_ActiveObject active)
            {
                info["runtime_message_level"] = active.RuntimeMessageLevel.ToString();
                warningCount += active.RuntimeMessages(GH_RuntimeMessageLevel.Warning).Count;
                errorCount += active.RuntimeMessages(GH_RuntimeMessageLevel.Error).Count;
            }
            info["graph_id"] = graphId;
            info["alias"] = aliases.TryGetValue(item.InstanceGuid, out var alias) ? alias : null;
            info["output_count"] = outputs.Length;
            info["outputs_with_data"] = outputs.Count(HasData);
            info["outputs"] = outputs.Select((port, index) => Port(port, index, false, false, 0)).ToArray();
            AddSpecialState(item, info);
            diagnostics.Add(info);
        }
        return new Dictionary<string, object?>
        {
            ["object_count"] = diagnostics.Count,
            ["output_count"] = allOutputs.Count,
            ["outputs_with_data"] = allOutputs.Count(HasData),
            ["total_output_data_count"] = allOutputs.Sum(parameter => parameter.VolatileData.DataCount),
            ["warning_count"] = warningCount,
            ["error_count"] = errorCount,
            ["objects"] = diagnostics,
        };
    }

    private static Dictionary<string, object?> ConnectionEnd(IGH_Param port) => new()
    {
        ["component_id"] = port.Attributes?.GetTopLevel.DocObject.InstanceGuid.ToString("D"),
        ["param_name"] = port.Name,
        ["param_nickname"] = port.NickName,
    };

    internal static bool HasData(IGH_Param parameter) =>
        parameter.VolatileDataCount > 0 || parameter.VolatileData.DataCount > 0;

    internal static Dictionary<string, object?> ParameterData(IGH_Param parameter, int maxItems)
    {
        var tree = parameter.VolatileData;
        var branches = new List<Dictionary<string, object?>>();
        var consumed = 0;
        foreach (var path in tree.Paths)
        {
            var items = new List<object?>();
            foreach (var item in tree.get_Branch(path))
            {
                if (consumed >= maxItems)
                    break;
                items.Add(Value(item));
                consumed++;
            }
            branches.Add(new Dictionary<string, object?> { ["path"] = path.ToString(), ["items"] = items });
            if (consumed >= maxItems)
                break;
        }
        return new Dictionary<string, object?>
        {
            ["data_count"] = tree.DataCount,
            ["branch_count"] = tree.PathCount,
            ["values"] = branches,
            ["truncated"] = tree.DataCount > maxItems,
        };
    }

    internal static object? Value(object? item) => item switch
    {
        null => null,
        GH_Number number => number.Value,
        GH_Integer integer => integer.Value,
        GH_Boolean boolean => boolean.Value,
        GH_String text => text.Value,
        GH_Point point => NativeFunctions.PointArray(point.Value),
        GH_Vector vector => Vector(vector.Value),
        GH_Plane plane => new Dictionary<string, object?>
        {
            ["origin"] = NativeFunctions.PointArray(plane.Value.Origin),
            ["x_axis"] = Vector(plane.Value.XAxis),
            ["y_axis"] = Vector(plane.Value.YAxis),
            ["z_axis"] = Vector(plane.Value.ZAxis),
        },
        GH_Line line => new Dictionary<string, object?>
        {
            ["from"] = NativeFunctions.PointArray(line.Value.From),
            ["to"] = NativeFunctions.PointArray(line.Value.To),
        },
        GH_Circle circle => new Dictionary<string, object?>
        {
            ["center"] = NativeFunctions.PointArray(circle.Value.Center),
            ["radius"] = circle.Value.Radius,
        },
        GH_Curve curve => new Dictionary<string, object?>
        {
            ["type"] = "Curve",
            ["is_closed"] = curve.Value?.IsClosed ?? false,
            ["length"] = curve.Value?.GetLength() ?? 0,
        },
        GH_Surface => new Dictionary<string, object?> { ["type"] = "Surface" },
        GH_Brep brep => new Dictionary<string, object?>
        {
            ["type"] = "Brep",
            ["is_solid"] = brep.Value?.IsSolid ?? false,
            ["faces"] = brep.Value?.Faces.Count ?? 0,
        },
        GH_Mesh mesh => new Dictionary<string, object?>
        {
            ["type"] = "Mesh",
            ["vertices"] = mesh.Value?.Vertices.Count ?? 0,
            ["faces"] = mesh.Value?.Faces.Count ?? 0,
        },
        IGH_Goo goo => new Dictionary<string, object?>
        {
            ["type"] = goo.TypeName,
            ["description"] = goo.ToString(),
        },
        _ => item.ToString(),
    };

    private static double[] Vector(Vector3d value) => new[] { value.X, value.Y, value.Z };
}
