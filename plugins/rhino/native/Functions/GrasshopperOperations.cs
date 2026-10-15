using System.Drawing;
using System.Reflection;
using System.Text.Json;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperOperations
{
    internal static T Mutate<T>(
        GH_Document document,
        string description,
        Func<T> operation,
        bool rollbackOnError = true
    )
    {
        var undoCount = document.UndoServer.UndoCount;
        var graphMetadata = CaptureGraphMetadata(document);
        try
        {
            var result = operation();
            MergeUndoRecords(document, undoCount);
            return result;
        }
        catch
        {
            if (!rollbackOnError)
            {
                MergeUndoRecords(document, undoCount);
                throw;
            }

            try
            {
                var undoEvents = Math.Max(0, document.UndoServer.UndoCount - undoCount);
                if (undoEvents > 1)
                {
                    document.UndoUtil.MergeRecords(undoEvents);
                }

                if (document.UndoServer.UndoCount > undoCount)
                {
                    document.UndoServer.PerformUndo();
                }

                RestoreGraphMetadata(document, graphMetadata);
            }
            catch
            {
                throw new NativeCommandException(
                    "grasshopperRollbackFailed",
                    "Grasshopper could not roll back the failed operation."
                );
            }

            throw;
        }
    }

    internal static void MergeUndoRecords(GH_Document document, int previousCount)
    {
        var createdRecords = Math.Max(0, document.UndoServer.UndoCount - previousCount);
        if (createdRecords > 1)
        {
            document.UndoUtil.MergeRecords(createdRecords);
        }
    }

    internal static void RecordAdd(GH_Document document, IGH_DocumentObject item, string description) =>
        document.UndoUtil.RecordAddObjectEvent(description, item);

    internal static void RecordChange(GH_Document document, IGH_DocumentObject item, string description) =>
        document.UndoUtil.RecordGenericObjectEvent(description, item);

    internal static void RecordWire(GH_Document document, IGH_Param parameter, string description) =>
        document.UndoUtil.RecordWireEvent(description, parameter);

    internal static bool AddObject(GH_Document document, IGH_DocumentObject item, string description)
    {
        RecordAdd(document, item, description);
        if (!document.AddObject(item, update: false))
        {
            throw new NativeCommandException("componentAddFailed", "Grasshopper could not add the requested object.");
        }

        return true;
    }

    internal static bool RemoveObjects(
        GH_Document document,
        IEnumerable<IGH_DocumentObject> objects,
        string description
    )
    {
        var seen = new HashSet<Guid>();
        var items = objects
            .Where(item => item is not null && seen.Add(item.InstanceGuid))
            .ToArray();
        if (items.Length == 0)
        {
            return false;
        }

        document.UndoUtil.RecordRemoveObjectEvent(description, items);
        foreach (var item in items)
        {
            if (!document.RemoveObject(item, update: false))
            {
                throw new NativeCommandException("componentDeleteFailed", "Grasshopper could not remove an object.");
            }

            ForgetAliases(document, item.InstanceGuid);
        }

        return true;
    }

    internal static IGH_DocumentObject ResolveSelector(
        GH_Document document,
        string selector,
        string? graphId = null,
        IReadOnlyDictionary<string, IGH_DocumentObject>? requestAliases = null
    )
    {
        var text = selector.Trim();
        if (Guid.TryParse(text, out var id))
        {
            return document.Objects.FirstOrDefault(item => item.InstanceGuid == id)
                ?? throw new NativeCommandException("componentNotFound", "The Grasshopper canvas object was not found.");
        }

        if (requestAliases is not null && requestAliases.TryGetValue(text, out var localAlias))
        {
            return localAlias;
        }

        if (!string.IsNullOrWhiteSpace(graphId))
        {
            var stored = document.ValueTable.GetValue(GrasshopperHelpers.AliasKey(graphId!, text), string.Empty);
            if (Guid.TryParse(stored, out var storedId))
            {
                var aliasObject = document.Objects.FirstOrDefault(item => item.InstanceGuid == storedId);
                if (aliasObject is not null)
                {
                    return aliasObject;
                }
            }
        }

        var matches = document.Objects
            .Where(item => string.Equals(item.NickName, text, StringComparison.OrdinalIgnoreCase)
                || string.Equals(item.Name, text, StringComparison.OrdinalIgnoreCase))
            .Take(2)
            .ToArray();
        return matches.Length switch
        {
            1 => matches[0],
            0 => throw new NativeCommandException("componentNotFound", "The Grasshopper canvas object was not found."),
            _ => throw new NativeCommandException("ambiguousComponentName", "The canvas nickname is not unique; use its instance identifier."),
        };
    }

    internal static IGH_DocumentObject ResolveSelector(
        GH_Document document,
        JsonElement parameters,
        string idKey,
        string nicknameKey,
        string? graphId = null
    ) => GrasshopperHelpers.RequireObject(document, parameters, graphId ?? string.Empty, idKey, nicknameKey);

    internal static IGH_Param ResolvePort(
        IGH_DocumentObject owner,
        JsonElement parameters,
        string direction,
        string indexKey,
        string nameKey
    ) => GrasshopperHelpers.RequirePort(
        direction == "input" ? GrasshopperHelpers.Inputs(owner) : GrasshopperHelpers.Outputs(owner),
        parameters,
        indexKey,
        nameKey
    );

    internal static void SetPosition(IGH_DocumentObject item, JsonElement position)
    {
        if (position.ValueKind != JsonValueKind.Array || position.GetArrayLength() != 2)
        {
            throw new NativeCommandException("invalidParameters", "A canvas position must contain two coordinates.");
        }

        var coordinates = position.EnumerateArray().Select(NativeFunctions.ReadFiniteNumber).ToArray();
        if (item.Attributes is null)
        {
            item.CreateAttributes();
        }

        var attributes = item.Attributes
            ?? throw new NativeCommandException("componentInfoUnavailable", "Grasshopper could not create canvas attributes for the selected object.");
        attributes.Pivot = new PointF((float)coordinates[0], (float)coordinates[1]);
    }

    internal static void ApplyObjectOptions(
        IGH_DocumentObject item,
        JsonElement parameters,
        bool includeValue
    )
    {
        if (NativeFunctions.Has(parameters, "nickname"))
        {
            var nickname = NativeFunctions.Text(parameters, "nickname");
            if (nickname is null || nickname.Trim().Length == 0)
            {
                throw new NativeCommandException("invalidParameters", "The component nickname cannot be empty.");
            }

            item.NickName = nickname.Trim();
        }

        if (NativeFunctions.Has(parameters, "position"))
        {
            SetPosition(item, NativeFunctions.Property(parameters, "position"));
        }

        if (NativeFunctions.Has(parameters, "enabled") && item is IGH_ActiveObject active)
        {
            active.Locked = !NativeFunctions.Boolean(parameters, "enabled");
        }

        if (NativeFunctions.Has(parameters, "preview") && item is IGH_PreviewObject preview)
        {
            preview.Hidden = !NativeFunctions.Boolean(parameters, "preview");
        }

        if (!includeValue)
        {
            return;
        }

        var value = NativeFunctions.Property(parameters, "value");
        var content = NativeFunctions.Property(parameters, "content");
        if (content.ValueKind == JsonValueKind.Undefined)
        {
            content = NativeFunctions.Property(parameters, "text");
        }

        if (content.ValueKind != JsonValueKind.Undefined)
        {
            value = content;
        }

        if (value.ValueKind != JsonValueKind.Undefined)
        {
            SetValue(item, value, parameters);
        }
    }

    internal static object? SetValue(
        IGH_DocumentObject target,
        JsonElement value,
        JsonElement options,
        GH_Document? document = null
    )
    {
        if (document is not null && target is GH_NumberSlider or GH_BooleanToggle or GH_Panel or GH_ValueList)
        {
            RecordChange(document, target, "Set MCP Grasshopper parameter value");
        }

        if (target is GH_NumberSlider slider)
        {
            SetSliderValue(slider, value, options);
            return slider.CurrentValue;
        }

        if (target is GH_BooleanToggle toggle)
        {
            if (value.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            {
                throw new NativeCommandException("invalidParameters", "A Boolean Toggle value must be true or false.");
            }

            toggle.Value = value.GetBoolean();
            return toggle.Value;
        }

        if (target is GH_Panel panel)
        {
            if (value.ValueKind != JsonValueKind.String)
            {
                throw new NativeCommandException("invalidParameters", "A Panel value must be a string.");
            }

            panel.SetUserText(value.GetString() ?? string.Empty);
            return panel.UserText;
        }

        if (target is GH_ValueList valueList)
        {
            return SelectValueListItem(valueList, value);
        }

        var parameter = target as IGH_Param;
        if (parameter is null && target is GH_Component)
        {
            var ports = GrasshopperHelpers.Inputs(target);
            parameter = GrasshopperHelpers.RequirePort(
                ports,
                options,
                "input_index",
                "input_name"
            );
        }

        if (parameter is null)
        {
            throw new NativeCommandException("unsupportedParameterType", "The selected Grasshopper object does not accept a direct value.");
        }

        return SetPersistentValue(parameter, value);
    }

    internal static void Recompute(GH_Document document, bool expireAll) => document.NewSolution(expireAll);

    internal static string? Selector(JsonElement parameters, string primary, string fallback) =>
        NativeFunctions.Text(parameters, primary) ?? NativeFunctions.Text(parameters, fallback);

    private static void SetSliderValue(GH_NumberSlider slider, JsonElement value, JsonElement options)
    {
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetDecimal(out var number))
        {
            throw new NativeCommandException("invalidParameters", "A Number Slider value must be a finite number.");
        }

        if (NativeFunctions.Has(options, "min"))
        {
            slider.Slider.Minimum = DecimalOption(options, "min");
        }

        if (NativeFunctions.Has(options, "max"))
        {
            slider.Slider.Maximum = DecimalOption(options, "max");
        }

        if (NativeFunctions.Has(options, "decimals"))
        {
            slider.Slider.DecimalPlaces = Math.Max(0, Math.Min(12, NativeFunctions.Integer(options, "decimals")));
        }

        if (slider.Slider.Minimum > slider.Slider.Maximum)
        {
            throw new NativeCommandException("invalidParameters", "The Number Slider minimum cannot exceed its maximum.");
        }

        if (number < slider.Slider.Minimum || number > slider.Slider.Maximum || !slider.TrySetSliderValue(number))
        {
            throw new NativeCommandException("sliderValueOutOfRange", "The Number Slider value is outside its configured range.");
        }
    }

    private static decimal DecimalOption(JsonElement options, string key)
    {
        var value = NativeFunctions.Property(options, key);
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetDecimal(out var number))
        {
            throw new NativeCommandException("invalidParameters", "The Number Slider range is invalid.");
        }

        return number;
    }

    private static object SelectValueListItem(GH_ValueList valueList, JsonElement value)
    {
        var target = ToPlainObject(value);
        var expected = JsonSerializer.Serialize(target);
        var index = -1;
        for (var candidateIndex = 0; candidateIndex < valueList.ListItems.Count; candidateIndex++)
        {
            var item = valueList.ListItems[candidateIndex];
            var itemValue = GrasshopperHelpers.SerializeGoo(item.Value);
            var matchesText = value.ValueKind == JsonValueKind.String
                && (string.Equals(item.Name, value.GetString(), StringComparison.OrdinalIgnoreCase)
                    || string.Equals(item.Expression, value.GetString(), StringComparison.OrdinalIgnoreCase));
            if (matchesText || string.Equals(JsonSerializer.Serialize(itemValue), expected, StringComparison.Ordinal))
            {
                index = candidateIndex;
                break;
            }
        }

        if (index < 0)
        {
            throw new NativeCommandException("valueListItemNotFound", "The requested value is not available in this Value List.");
        }

        valueList.SelectItem(index);
        return valueList.ListItems[index].Name;
    }

    private static object? SetPersistentValue(IGH_Param parameter, JsonElement value)
    {
        var rawValue = ToPlainObject(value);
        var methods = parameter.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public)
            .Where(method => method.Name == "SetPersistentData")
            .Where(method => method.GetParameters().Length == 1)
            .ToArray();
        var arraySetter = methods.FirstOrDefault(method => method.GetParameters()[0].ParameterType == typeof(object[]));
        var objectSetter = methods.FirstOrDefault(method => method.GetParameters()[0].ParameterType == typeof(object));
        var setter = arraySetter ?? objectSetter;
        if (setter is null)
        {
            throw new NativeCommandException("unsupportedParameterType", "The selected Grasshopper parameter does not support persistent values.");
        }

        try
        {
            setter.Invoke(parameter, arraySetter is not null
                ? new object?[] { new[] { rawValue } }
                : new object?[] { rawValue });
        }
        catch (TargetInvocationException)
        {
            throw new NativeCommandException("invalidParameterValue", "Grasshopper could not convert the value for the selected parameter.");
        }

        return rawValue;
    }

    private static object? ToPlainObject(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Undefined or JsonValueKind.Null => null,
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.String => value.GetString(),
        JsonValueKind.Number when value.TryGetInt64(out var integer) => integer,
        JsonValueKind.Number when value.TryGetDouble(out var number) => number,
        JsonValueKind.Array => value.EnumerateArray().Select(ToPlainObject).ToArray(),
        JsonValueKind.Object => value.EnumerateObject().ToDictionary(
            item => item.Name,
            item => ToPlainObject(item.Value),
            StringComparer.Ordinal
        ),
        _ => throw new NativeCommandException("invalidParameters", "The Grasshopper value could not be decoded."),
    };

    private static void ForgetAliases(GH_Document document, Guid instanceId)
    {
        var id = instanceId.ToString("D");
        foreach (var key in document.ValueTable.EntryNames()
            .Where(name => name.IndexOf(".alias.", StringComparison.Ordinal) >= 0
                && string.Equals(document.ValueTable.GetValue(name, string.Empty), id, StringComparison.OrdinalIgnoreCase))
            .ToArray())
        {
            document.ValueTable.DeleteValue(key);
        }

        foreach (var key in document.ValueTable.EntryNames()
            .Where(name => (name.EndsWith(".group", StringComparison.Ordinal)
                || name.IndexOf(".group.", StringComparison.Ordinal) >= 0)
                && string.Equals(document.ValueTable.GetValue(name, string.Empty), id, StringComparison.OrdinalIgnoreCase))
            .ToArray())
        {
            document.ValueTable.DeleteValue(key);
        }
    }

    private static Dictionary<string, string> CaptureGraphMetadata(GH_Document document)
    {
        const string prefix = "rhino_mcp.graph.";
        return document.ValueTable.EntryNames()
            .Where(name => name.StartsWith(prefix, StringComparison.Ordinal))
            .ToDictionary(name => name, name => document.ValueTable.GetValue(name, string.Empty), StringComparer.Ordinal);
    }

    private static void RestoreGraphMetadata(GH_Document document, IReadOnlyDictionary<string, string> snapshot)
    {
        const string prefix = "rhino_mcp.graph.";
        foreach (var name in document.ValueTable.EntryNames()
            .Where(name => name.StartsWith(prefix, StringComparison.Ordinal))
            .ToArray())
        {
            document.ValueTable.DeleteValue(name);
        }

        foreach (var entry in snapshot)
        {
            document.ValueTable.SetValue(entry.Key, entry.Value);
        }
    }
}
