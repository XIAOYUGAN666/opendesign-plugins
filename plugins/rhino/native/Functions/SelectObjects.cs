using System.Text.Json;
using Rhino.DocObjects;

namespace RhinoMcpBridge.Functions;

internal static class SelectObjects
{
    [McpCommand("select_objects", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Select(RequestContext context, JsonElement parameters)
    {
        var filters = NativeFunctions.Property(parameters, "filters");
        var mode = NativeFunctions.Text(parameters, "filters_type", "and");
        if (mode is not ("and" or "or"))
            throw new NativeCommandException("invalid_argument", "Pass either and or or for filters_type.");

        var document = context.RhinoDocument;
        var settings = new ObjectEnumeratorSettings { NormalObjects = true };
        var predicates = new List<Func<RhinoObject, bool>>();
        if (filters.ValueKind == JsonValueKind.Object)
        {
            foreach (var filter in filters.EnumerateObject())
            {
                switch (filter.Name)
                {
                    case "name":
                        var names = Values(filter.Value);
                        predicates.Add(obj => obj.Name is string name && names.Contains(name));
                        break;
                    case "color":
                        if (!NativeFunctions.TryColor(filters, "color", out var color))
                            throw new NativeCommandException("invalid_argument", "color must be a single RGB triplet.");
                        predicates.Add(obj => obj.Attributes.ObjectColor.R == color.R
                            && obj.Attributes.ObjectColor.G == color.G && obj.Attributes.ObjectColor.B == color.B);
                        break;
                    case "ids":
                        var ids = new HashSet<Guid>();
                        foreach (var idText in Values(filter.Value))
                        {
                            if (!Guid.TryParse(idText, out var id))
                                throw new NativeCommandException("invalid_argument", "An object ID is not a valid GUID.");
                            if (NativeFunctions.FindObject(context, id) is null)
                                throw new NativeCommandException("not_found", $"Object '{id}' not found.");
                            ids.Add(id);
                        }
                        predicates.Add(obj => ids.Contains(obj.Id));
                        break;
                    case "layer":
                        var layerName = filter.Value.GetString();
                        var layer = document.Layers.FirstOrDefault(item => !item.IsDeleted &&
                            string.Equals(item.FullPath, layerName, StringComparison.OrdinalIgnoreCase))
                            ?? document.Layers.FirstOrDefault(item => !item.IsDeleted &&
                                string.Equals(item.Name, layerName, StringComparison.OrdinalIgnoreCase))
                            ?? throw new NativeCommandException("not_found", $"Layer '{layerName}' not found.");
                        predicates.Add(obj => obj.Attributes.LayerIndex == layer.Index);
                        break;
                    case "type":
                        var types = new HashSet<string>(Values(filter.Value), StringComparer.OrdinalIgnoreCase);
                        predicates.Add(obj => types.Contains(GetDocumentSummary.NormalizedType(obj)));
                        break;
                    case "hidden":
                        var hidden = filter.Value.GetBoolean();
                        settings.HiddenObjects = hidden;
                        predicates.Add(obj => (obj.IsHidden
                            || document.Layers.FindIndex(obj.Attributes.LayerIndex)?.IsVisible == false) == hidden);
                        break;
                    case "locked":
                        var locked = filter.Value.GetBoolean();
                        settings.LockedObjects = locked;
                        predicates.Add(obj => (obj.IsLocked
                            || document.Layers.FindIndex(obj.Attributes.LayerIndex)?.IsLocked == true) == locked);
                        break;
                    default:
                        var values = Values(filter.Value);
                        var key = filter.Name;
                        predicates.Add(obj => obj.Attributes.GetUserString(key) is string value && values.Contains(value));
                        break;
                }
            }
        }

        var matches = document.Objects.GetObjectList(settings)
            .Where(obj => predicates.Count == 0
                || (mode == "or" ? predicates.Any(predicate => predicate(obj)) : predicates.All(predicate => predicate(obj))))
            .ToArray();
        context.CancellationToken.ThrowIfCancellationRequested();
        document.Objects.UnselectAll();
        var selected = matches.Count(obj => obj.Select(true, true, true, false, true, true) > 0);
        document.Views.Redraw();
        return new Dictionary<string, object?> { ["count"] = selected, ["matched"] = matches.Length };
    }

    private static HashSet<string> Values(JsonElement value) =>
        value.ValueKind == JsonValueKind.String
            ? new HashSet<string> { value.GetString()! }
            : new HashSet<string>(value.EnumerateArray().Select(item => item.GetString()!));
}
