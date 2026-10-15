using System.Drawing;
using System.Text.Json;
using Grasshopper;
using Grasshopper.GUI.Canvas;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;
using Grasshopper.Kernel.Types;
using Rhino;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperGraphHelpers
{
    internal static string RequireSelector(JsonElement value, string key)
    {
        var selector = NativeFunctions.Text(value, key)?.Trim();
        if (selector is null || selector.Length == 0)
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper object selector is required.");
        }

        return selector;
    }

    internal static IGH_DocumentObject ResolveSelector(
        GH_Document document,
        JsonElement value,
        string key,
        string? graphId,
        IReadOnlyDictionary<string, IGH_DocumentObject> aliases
    ) => GrasshopperOperations.ResolveSelector(
        document,
        RequireSelector(value, key),
        graphId,
        aliases
    );

    internal static IGH_Param ResolvePort(
        IGH_DocumentObject owner,
        JsonElement value,
        string direction,
        string indexKey,
        string nameKey
    ) => GrasshopperOperations.ResolvePort(owner, value, direction, indexKey, nameKey);

    internal static Dictionary<string, object?> Connect(
        GH_Document document,
        JsonElement connection,
        string? graphId,
        IReadOnlyDictionary<string, IGH_DocumentObject> aliases
    )
    {
        var sourceOwner = ResolveSelector(document, connection, "source", graphId, aliases);
        var targetOwner = ResolveSelector(document, connection, "target", graphId, aliases);
        var source = ResolvePort(sourceOwner, connection, "output", "source_output_index", "source_output_name");
        var target = ResolvePort(targetOwner, connection, "input", "target_input_index", "target_input_name");
        if (ReferenceEquals(source, target))
        {
            throw new NativeCommandException("invalidConnection", "A Grasshopper parameter cannot connect to itself.");
        }

        var alreadyConnected = target.Sources.Any(item => ReferenceEquals(item, source));
        if (!alreadyConnected)
        {
            GrasshopperOperations.RecordWire(document, target, "Connect MCP Grasshopper components");
            target.AddSource(source);
        }

        return new Dictionary<string, object?>
        {
            ["source_instance_id"] = sourceOwner.InstanceGuid.ToString("D"),
            ["source_output_index"] = Array.IndexOf(GrasshopperHelpers.Outputs(sourceOwner), source),
            ["source_output_name"] = source.NickName,
            ["target_instance_id"] = targetOwner.InstanceGuid.ToString("D"),
            ["target_input_index"] = Array.IndexOf(GrasshopperHelpers.Inputs(targetOwner), target),
            ["target_input_name"] = target.NickName,
            ["connected"] = true,
            ["already_connected"] = alreadyConnected,
        };
    }

    internal static Dictionary<string, object?> Disconnect(
        GH_Document document,
        JsonElement connection,
        string? graphId,
        IReadOnlyDictionary<string, IGH_DocumentObject> aliases
    )
    {
        var targetOwner = ResolveSelector(document, connection, "target", graphId, aliases);
        var target = ResolvePort(targetOwner, connection, "input", "target_input_index", "target_input_name");
        var disconnectAll = NativeFunctions.Boolean(connection, "disconnect_all");
        var sources = disconnectAll
            ? target.Sources.ToArray()
            : ResolveSource(document, target, connection, graphId, aliases);
        if (sources.Length == 0)
        {
            return new Dictionary<string, object?>
            {
                ["target_instance_id"] = targetOwner.InstanceGuid.ToString("D"),
                ["disconnected_count"] = 0,
                ["disconnected"] = false,
            };
        }

        GrasshopperOperations.RecordWire(document, target, "Disconnect MCP Grasshopper components");
        foreach (var source in sources)
        {
            target.RemoveSource(source);
        }

        return new Dictionary<string, object?>
        {
            ["target_instance_id"] = targetOwner.InstanceGuid.ToString("D"),
            ["disconnected_count"] = sources.Length,
            ["disconnected"] = true,
        };
    }

    internal static IGH_DocumentObject CreateComponent(
        GH_Document document,
        JsonElement componentSpec,
        Dictionary<string, IGH_DocumentObject> aliases,
        string? graphId
    )
    {
        var alias = RequireSelector(componentSpec, "alias");
        if (!System.Text.RegularExpressions.Regex.IsMatch(alias, "^[A-Za-z_][A-Za-z0-9_-]*$"))
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper graph alias has an invalid format.");
        }

        if (aliases.ContainsKey(alias)
            || (graphId is not null
                && !string.IsNullOrWhiteSpace(graphId)
                && GrasshopperHelpers.GraphAliases(document, graphId).ContainsKey(alias)))
        {
            throw new NativeCommandException("duplicateGraphAlias", "The Grasshopper graph alias is already in use.");
        }

        var name = NativeFunctions.Text(componentSpec, "component_name");
        var guid = NativeFunctions.Text(componentSpec, "component_guid")
            ?? NativeFunctions.Text(componentSpec, "guid");
        var proxy = GrasshopperHelpers.RequireProxy(name, guid);
        var item = GrasshopperHelpers.CreateInstance(proxy);
        GrasshopperOperations.ApplyObjectOptions(item, componentSpec, includeValue: true);
        GrasshopperOperations.AddObject(document, item, "Build MCP Grasshopper graph");
        aliases.Add(alias, item);
        if (graphId is not null && !string.IsNullOrWhiteSpace(graphId))
        {
            GrasshopperHelpers.StoreAlias(document, graphId, alias, item.InstanceGuid);
        }

        return item;
    }

    internal static object? SetValue(
        GH_Document document,
        JsonElement valueUpdate,
        string? graphId,
        IReadOnlyDictionary<string, IGH_DocumentObject> aliases
    )
    {
        var target = ResolveSelector(document, valueUpdate, "target", graphId, aliases);
        var value = NativeFunctions.Property(valueUpdate, "value");
        if (value.ValueKind == JsonValueKind.Undefined)
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper value update requires a value.");
        }

        return GrasshopperOperations.SetValue(target, value, valueUpdate, document);
    }

    internal static object[] ApplyGroups(
        GH_Document document,
        JsonElement groups,
        Dictionary<string, IGH_DocumentObject> aliases,
        string? graphId
    )
    {
        if (groups.ValueKind == JsonValueKind.Undefined || groups.ValueKind == JsonValueKind.Null)
        {
            return Array.Empty<object>();
        }

        if (groups.ValueKind != JsonValueKind.Array)
        {
            throw new NativeCommandException("invalidParameters", "Grasshopper groups must be an array.");
        }

        var created = new List<object>();
        foreach (var groupSpec in groups.EnumerateArray())
        {
            var displayName = NativeFunctions.Text(groupSpec, "name")
                ?? NativeFunctions.Text(groupSpec, "label")
                ?? NativeFunctions.Text(groupSpec, "nickname");
            if (displayName is null || string.IsNullOrWhiteSpace(displayName))
            {
                throw new NativeCommandException("invalidParameters", "A Grasshopper group name is required.");
            }

            var targets = ReadStringArray(groupSpec, "targets");
            if (targets.Length == 0)
            {
                throw new NativeCommandException("invalidParameters", "A Grasshopper group must include at least one object.");
            }

            var objects = targets
                .Select(selector => GrasshopperOperations.ResolveSelector(document, selector, graphId, aliases))
                .GroupBy(item => item.InstanceGuid)
                .Select(group => group.First())
                .ToArray();
            var group = new GH_Group { NickName = displayName.Trim() };
            var color = NativeFunctions.Property(groupSpec, "color");
            if (color.ValueKind == JsonValueKind.Array && color.GetArrayLength() == 3)
            {
                var channels = color.EnumerateArray().Select(NativeFunctions.ReadFiniteNumber).ToArray();
                if (channels.Any(channel => channel < 0 || channel > 255 || Math.Truncate(channel) != channel))
                {
                    throw new NativeCommandException("invalidParameters", "Group color channels must be integers from 0 to 255.");
                }

                group.Colour = Color.FromArgb((int)channels[0], (int)channels[1], (int)channels[2]);
            }

            GrasshopperOperations.AddObject(document, group, "Create MCP Grasshopper group");
            GrasshopperOperations.RecordChange(document, group, "Add objects to MCP Grasshopper group");
            foreach (var item in objects)
            {
                group.AddObject(item.InstanceGuid);
            }

            group.ExpireCaches();
            var groupAlias = NativeFunctions.Text(groupSpec, "alias") ?? displayName.Trim();
            if (aliases.ContainsKey(groupAlias))
            {
                throw new NativeCommandException("duplicateGraphAlias", "The Grasshopper group alias is already in use.");
            }

            aliases[groupAlias] = group;
            if (graphId is not null && !string.IsNullOrWhiteSpace(graphId))
            {
                GrasshopperHelpers.StoreGraphGroup(document, graphId, group.InstanceGuid, groupAlias);
            }

            created.Add(GrasshopperHelpers.ObjectInfo(group));
        }

        return created.ToArray();
    }

    internal static object[] ApplyPreviewPolicy(
        GH_Document document,
        JsonElement policy,
        Dictionary<string, IGH_DocumentObject> aliases,
        string? graphId
    )
    {
        if (policy.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
        {
            return Array.Empty<object>();
        }

        var mode = NativeFunctions.Text(policy, "mode");
        if (mode is not ("only" or "show" or "hide"))
        {
            throw new NativeCommandException("invalidParameters", "The Grasshopper preview policy mode is invalid.");
        }

        var targets = ReadStringArray(policy, "targets")
            .Select(selector => GrasshopperOperations.ResolveSelector(document, selector, graphId, aliases))
            .GroupBy(item => item.InstanceGuid)
            .Select(group => group.First())
            .ToArray();
        var targetIds = targets.Select(item => item.InstanceGuid).ToHashSet();
        var scopeSelectors = ReadStringArray(policy, "scope");
        var scope = scopeSelectors.Length > 0
            ? scopeSelectors.Select(selector => GrasshopperOperations.ResolveSelector(document, selector, graphId, aliases)).ToArray()
            : graphId is not null && !string.IsNullOrWhiteSpace(graphId)
                ? GrasshopperHelpers.GraphObjects(document, graphId).ToArray()
                : document.Objects.ToArray();
        if (mode is "show" or "hide")
        {
            scope = targets;
        }

        var changed = new List<object>();
        foreach (var item in scope.OfType<IGH_PreviewObject>())
        {
            var shouldShow = mode == "only" ? targetIds.Contains(((IGH_DocumentObject)item).InstanceGuid)
                : mode == "show";
            var hidden = !shouldShow;
            if (item.Hidden == hidden)
            {
                continue;
            }

            GrasshopperOperations.RecordChange(document, (IGH_DocumentObject)item, "Update MCP Grasshopper preview visibility");
            item.Hidden = hidden;
            changed.Add(new Dictionary<string, object?>
            {
                ["instance_id"] = ((IGH_DocumentObject)item).InstanceGuid.ToString("D"),
                ["preview"] = shouldShow,
            });
        }

        if (mode != "hide")
        {
            foreach (var item in targets.OfType<IGH_PreviewObject>())
            {
                var owner = (IGH_DocumentObject)item;
                if (scope.Any(candidate => candidate.InstanceGuid == owner.InstanceGuid) || !item.Hidden)
                {
                    continue;
                }

                GrasshopperOperations.RecordChange(document, owner, "Update MCP Grasshopper preview visibility");
                item.Hidden = false;
                changed.Add(new Dictionary<string, object?>
                {
                    ["instance_id"] = owner.InstanceGuid.ToString("D"),
                    ["preview"] = true,
                });
            }
        }

        return changed.ToArray();
    }

    internal static object[] ApplyLayout(
        GH_Document document,
        JsonElement layout,
        Dictionary<string, IGH_DocumentObject> aliases,
        string? graphId
    )
    {
        if (layout.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null or JsonValueKind.False)
        {
            return Array.Empty<object>();
        }

        var enabled = layout.ValueKind == JsonValueKind.True
            || NativeFunctions.Boolean(layout, "enabled", true);
        if (!enabled)
        {
            return Array.Empty<object>();
        }

        var selectors = ReadStringArray(layout, "targets");
        var includeGroups = NativeFunctions.Boolean(layout, "include_groups");
        var selected = selectors.Length > 0
            ? selectors.Select(selector => GrasshopperOperations.ResolveSelector(document, selector, graphId, aliases)).ToArray()
            : graphId is not null && !string.IsNullOrWhiteSpace(graphId)
                ? GrasshopperHelpers.GraphObjects(document, graphId).ToArray()
                : aliases.Values.GroupBy(item => item.InstanceGuid).Select(group => group.First()).ToArray();
        var objects = selected.Where(item => includeGroups || item is not GH_Group).ToArray();
        var start = ReadPosition(layout, "start_position", 40, 40);
        var xSpacing = GrasshopperComponents.ReadSpacing(layout, "x_spacing", 220);
        var ySpacing = GrasshopperComponents.ReadSpacing(layout, "y_spacing", 90);
        var maxColumns = Math.Max(1, Math.Min(100, NativeFunctions.Integer(layout, "max_columns", 100)));
        var positions = GrasshopperComponents.Layout(document, objects, start, xSpacing, ySpacing, maxColumns);
        foreach (var item in objects)
        {
            GrasshopperOperations.RecordChange(document, item, "Layout MCP Grasshopper graph");
            GrasshopperOperations.SetPosition(item, JsonSerializer.SerializeToElement(positions[item.InstanceGuid]));
        }

        return objects.Select(item => (object)new Dictionary<string, object?>
        {
            ["instance_id"] = item.InstanceGuid.ToString("D"),
            ["position"] = positions[item.InstanceGuid],
        }).ToArray();
    }

    internal static bool OpenCanvas(RequestContext context, GH_Document document, bool openCanvas)
    {
        return RequestContextFactory.TryOpenGrasshopperDocument(
            context,
            document,
            openCanvas,
            makeActive: false
        );
    }

    internal static object Verify(
        GH_Document document,
        JsonElement verification,
        string? graphId,
        IReadOnlyDictionary<string, IGH_DocumentObject> aliases
    )
    {
        if (verification.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
        {
            return new Dictionary<string, object?> { ["passed"] = true, ["errors"] = Array.Empty<string>() };
        }

        if (NativeFunctions.Boolean(verification, "run_solution", true))
        {
            document.NewSolution(false);
        }

        var errors = new List<string>();
        var outputs = NativeFunctions.Property(verification, "outputs");
        if (outputs.ValueKind == JsonValueKind.Array)
        {
            foreach (var output in outputs.EnumerateArray())
            {
                VerifyOutput(document, output, graphId, aliases, errors);
            }
        }

        if (NativeFunctions.Boolean(verification, "expect_no_runtime_warnings"))
        {
            var warnings = document.Objects.OfType<IGH_ActiveObject>()
                .SelectMany(active => active.RuntimeMessages(GH_RuntimeMessageLevel.Warning))
                .ToArray();
            if (warnings.Length > 0)
            {
                errors.Add($"Expected no runtime warnings, but found {warnings.Length}.");
            }
        }

        return new Dictionary<string, object?>
        {
            ["passed"] = errors.Count == 0,
            ["errors"] = errors,
            ["checked_output_count"] = outputs.ValueKind == JsonValueKind.Array ? outputs.GetArrayLength() : 0,
        };
    }

    internal static bool FailOnVerificationError(JsonElement parameters, object verification)
    {
        if (!NativeFunctions.Boolean(parameters, "fail_on_verification_error"))
        {
            return false;
        }

        var passed = verification is Dictionary<string, object?> result
            && result.TryGetValue("passed", out var value)
            && value is true;
        if (!passed)
        {
            throw new NativeCommandException("graphVerificationFailed", "The Grasshopper graph did not pass requested verification.");
        }

        return true;
    }

    internal static string[] ReadStringArray(JsonElement value, string key)
    {
        var array = NativeFunctions.Property(value, key);
        if (array.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
        {
            return Array.Empty<string>();
        }

        if (array.ValueKind != JsonValueKind.Array)
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper selector list must be an array.");
        }

        return array.EnumerateArray()
            .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : null)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Select(item => item!.Trim())
            .ToArray();
    }

    private static IGH_Param[] ResolveSource(
        GH_Document document,
        IGH_Param target,
        JsonElement connection,
        string? graphId,
        IReadOnlyDictionary<string, IGH_DocumentObject> aliases
    )
    {
        var selector = NativeFunctions.Text(connection, "source");
        if (selector is null || string.IsNullOrWhiteSpace(selector))
        {
            throw new NativeCommandException("invalidParameters", "A source selector is required unless disconnect_all is true.");
        }

        var owner = GrasshopperOperations.ResolveSelector(document, selector, graphId, aliases);
        var source = ResolvePort(owner, connection, "output", "source_output_index", "source_output_name");
        return target.Sources.Where(item => ReferenceEquals(item, source)).Take(1).ToArray();
    }

    private static double[] ReadPosition(JsonElement value, string key, double x, double y)
    {
        var position = NativeFunctions.Property(value, key);
        if (position.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
        {
            return new[] { x, y };
        }

        if (position.ValueKind != JsonValueKind.Array || position.GetArrayLength() != 2)
        {
            throw new NativeCommandException("invalidParameters", "A canvas position must contain two coordinates.");
        }

        return position.EnumerateArray().Select(NativeFunctions.ReadFiniteNumber).ToArray();
    }

    private static void VerifyOutput(
        GH_Document document,
        JsonElement outputSpec,
        string? graphId,
        IReadOnlyDictionary<string, IGH_DocumentObject> aliases,
        List<string> errors
    )
    {
        var selector = NativeFunctions.Text(outputSpec, "target") ?? NativeFunctions.Text(outputSpec, "alias");
        if (selector is null || string.IsNullOrWhiteSpace(selector))
        {
            errors.Add("An output verification entry has no target selector.");
            return;
        }

        try
        {
            var owner = GrasshopperOperations.ResolveSelector(document, selector, graphId, aliases);
            var output = ResolvePort(owner, outputSpec, "output", "output_index", "output_name");
            var count = output.VolatileDataCount;
            if (NativeFunctions.Has(outputSpec, "expect_count_min")
                && count < NativeFunctions.Integer(outputSpec, "expect_count_min"))
            {
                errors.Add($"Output '{selector}' has {count} items; the minimum expected count is {NativeFunctions.Integer(outputSpec, "expect_count_min")}.");
            }

            if (NativeFunctions.Has(outputSpec, "expect_count_exact")
                && count != NativeFunctions.Integer(outputSpec, "expect_count_exact"))
            {
                errors.Add($"Output '{selector}' has {count} items; expected exactly {NativeFunctions.Integer(outputSpec, "expect_count_exact")}.");
            }

            var items = output.VolatileData.Paths
                .SelectMany(path => output.VolatileData.get_Branch(path).OfType<IGH_Goo>())
                .ToArray();
            var expectedType = NativeFunctions.Text(outputSpec, "expect_type");
            var expectedAllType = NativeFunctions.Text(outputSpec, "expect_all_type");
            if (expectedType is not null && items.Length > 0 && !TypeMatches(items[0], expectedType))
            {
                errors.Add($"Output '{selector}' first item is not of expected type '{expectedType}'.");
            }

            if (expectedAllType is not null && items.Any(item => !TypeMatches(item, expectedAllType)))
            {
                errors.Add($"Output '{selector}' contains an item that is not of expected type '{expectedAllType}'.");
            }

            var expectSolid = NativeFunctions.Property(outputSpec, "expect_solid");
            var expectAllSolid = NativeFunctions.Property(outputSpec, "expect_all_solid");
            if (expectSolid.ValueKind == JsonValueKind.True && items.Length > 0 && !IsSolid(items[0]))
            {
                errors.Add($"Output '{selector}' first item is not a solid.");
            }

            if (expectAllSolid.ValueKind == JsonValueKind.True && items.Any(item => !IsSolid(item)))
            {
                errors.Add($"Output '{selector}' contains an item that is not a solid.");
            }

            if (NativeFunctions.Boolean(outputSpec, "expect_no_runtime_warnings"))
            {
                if (owner is IGH_ActiveObject active
                    && active.RuntimeMessages(GH_RuntimeMessageLevel.Warning).Count > 0)
                {
                    errors.Add($"Output '{selector}' owner has runtime warnings.");
                }
            }
        }
        catch (NativeCommandException exception)
        {
            errors.Add($"Output '{selector}' could not be verified ({exception.Category}).");
        }
    }

    private static bool TypeMatches(IGH_Goo item, string expected) =>
        string.Equals(item.TypeName, expected, StringComparison.OrdinalIgnoreCase)
        || string.Equals(item.GetType().Name, expected, StringComparison.OrdinalIgnoreCase)
        || string.Equals(item.ScriptVariable()?.GetType().Name, expected, StringComparison.OrdinalIgnoreCase);

    private static bool IsSolid(IGH_Goo item) => item.ScriptVariable() switch
    {
        Brep brep => brep.IsSolid,
        Extrusion extrusion => extrusion.ToBrep()?.IsSolid == true,
        Mesh mesh => mesh.IsClosed,
        _ => false,
    };
}
