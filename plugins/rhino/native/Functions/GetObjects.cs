using System.Text.Json;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class GetObjects
{
    [McpCommand("get_objects", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Get(RequestContext context, JsonElement parameters)
    {
        var offset = Math.Max(0, NativeFunctions.Integer(parameters, "offset"));
        var limit = Math.Min(200, Math.Max(1, NativeFunctions.Integer(parameters, "limit", 50)));
        var layerFilter = NativeFunctions.Text(parameters, "layer_filter");
        var typeFilter = NativeFunctions.Text(parameters, "type_filter");
        var bboxFilter = NativeFunctions.Property(parameters, "bbox_filter");
        var includeGeometry = NativeFunctions.Boolean(parameters, "include_geometry", true);
        var selectedOnly = NativeFunctions.Boolean(parameters, "selected_only");
        var includeHidden = NativeFunctions.Boolean(parameters, "include_hidden");
        var includeLocked = NativeFunctions.Boolean(parameters, "include_locked", true);
        var idFilter = NativeFunctions.Property(parameters, "ids");
        var ids = idFilter.ValueKind == JsonValueKind.Array
            ? new HashSet<Guid>(idFilter.EnumerateArray().Select(id =>
                Guid.TryParse(id.GetString(), out var parsed) ? parsed
                    : throw new NativeCommandException("invalid_argument", "An object ID is not a valid GUID.")))
            : null;

        BoundingBox? requestedBounds = null;
        if (bboxFilter.ValueKind == JsonValueKind.Array && bboxFilter.GetArrayLength() == 2)
        {
            var corners = NativeFunctions.Points(bboxFilter);
            requestedBounds = new BoundingBox(corners[0], corners[1]);
        }

        var candidates = includeHidden
            ? context.RhinoDocument.Objects.GetObjectList(new ObjectEnumeratorSettings { HiddenObjects = true })
            : NativeFunctions.Objects(context);
        var matches = candidates.Where(obj =>
        {
            var layer = context.RhinoDocument.Layers.FindIndex(obj.Attributes.LayerIndex);
            if ((!includeLocked && (obj.IsLocked || layer?.IsLocked == true))
                || (selectedOnly && obj.IsSelected(false) == 0)
                || (ids is not null && !ids.Contains(obj.Id)))
            {
                return false;
            }
            if (!string.IsNullOrWhiteSpace(layerFilter)
                && (layer is null || (!string.Equals(layer.Name, layerFilter, StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(layer.FullPath, layerFilter, StringComparison.OrdinalIgnoreCase))))
            {
                return false;
            }

            if (!string.IsNullOrWhiteSpace(typeFilter)
                && !string.Equals(GetDocumentSummary.NormalizedType(obj), typeFilter, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            if (requestedBounds is not null && !Overlaps(obj.Geometry.GetBoundingBox(accurate: true), requestedBounds.Value))
            {
                return false;
            }

            return true;
        }).ToArray();

        var filters = new Dictionary<string, object?>();
        if (!string.IsNullOrEmpty(layerFilter)) filters["layer"] = layerFilter;
        if (!string.IsNullOrEmpty(typeFilter)) filters["type"] = typeFilter;
        if (requestedBounds.HasValue) filters["bbox"] = NativeFunctions.BoundingBox(requestedBounds.Value);
        if (ids is not null) filters["id_count"] = ids.Count;
        if (selectedOnly) filters["selected_only"] = true;
        if (includeHidden) filters["include_hidden"] = true;
        if (!includeLocked) filters["include_locked"] = false;

        return new Dictionary<string, object?>
        {
            ["objects"] = matches.Skip(offset).Take(limit).Select(obj => NativeFunctions.ObjectInfo(obj, includeGeometry)).ToArray(),
            ["total_matching"] = matches.Length,
            ["offset"] = offset,
            ["limit"] = limit,
            ["has_more"] = offset + limit < matches.Length,
            ["filters"] = filters,
        };
    }

    private static bool Overlaps(BoundingBox first, BoundingBox second) =>
        first.IsValid && second.IsValid
        && first.Min.X <= second.Max.X && first.Max.X >= second.Min.X
        && first.Min.Y <= second.Max.Y && first.Max.Y >= second.Min.Y
        && first.Min.Z <= second.Max.Z && first.Max.Z >= second.Min.Z;
}
