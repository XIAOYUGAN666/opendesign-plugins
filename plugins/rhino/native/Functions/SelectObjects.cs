using System.Text.Json;
using Rhino.DocObjects;

namespace RhinoMcpBridge.Functions;

internal static class SelectObjects
{
    [McpCommand("select_objects", ReadOnly = false, SupportsDryRun = false)]
    internal static object Select(RequestContext context, JsonElement parameters)
    {
        var filters = NativeFunctions.Property(parameters, "filters");
        if (filters.ValueKind != JsonValueKind.Object || !filters.EnumerateObject().Any())
        {
            throw new NativeCommandException("invalidParameters", "At least one object filter is required.");
        }

        var predicates = new List<Func<RhinoObject, bool>>();
        foreach (var filter in filters.EnumerateObject())
        {
            if (filter.NameEquals("name"))
            {
                var names = filter.Value.EnumerateArray().Select(item => item.GetString() ?? string.Empty).ToArray();
                predicates.Add(obj => names.Contains(obj.Attributes.Name, StringComparer.OrdinalIgnoreCase));
            }
            else if (filter.NameEquals("color"))
            {
                var color = NativeFunctions.TryColor(filters, "color", out var parsedColor) ? parsedColor : System.Drawing.Color.Empty;
                predicates.Add(obj => obj.Attributes.ObjectColor.ToArgb() == color.ToArgb());
            }
            else
            {
                var expected = filter.Value.EnumerateArray().Select(item => item.GetString() ?? string.Empty).ToArray();
                predicates.Add(obj => expected.Contains(obj.Attributes.GetUserString(filter.Name) ?? string.Empty, StringComparer.Ordinal));
            }
        }

        var or = string.Equals(NativeFunctions.Text(parameters, "filters_type", "and"), "or", StringComparison.OrdinalIgnoreCase);
        var matches = NativeFunctions.Objects(context)
            .Where(obj => or ? predicates.Any(predicate => predicate(obj)) : predicates.All(predicate => predicate(obj)))
            .ToArray();
        context.CancellationToken.ThrowIfCancellationRequested();
        context.RhinoDocument.Objects.UnselectAll();
        var selected = matches.Count(obj => context.RhinoDocument.Objects.Select(obj.Id, select: true));
        context.RhinoDocument.Views.Redraw();
        return new Dictionary<string, object?> { ["count"] = selected };
    }
}
