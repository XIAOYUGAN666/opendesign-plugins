using System.Text.Json;
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
        BoundingBox? requestedBounds = null;
        if (bboxFilter.ValueKind == JsonValueKind.Array && bboxFilter.GetArrayLength() == 2)
        {
            var corners = NativeFunctions.Points(bboxFilter);
            requestedBounds = new BoundingBox(corners[0], corners[1]);
        }

        var matches = NativeFunctions.Objects(context).Where(obj =>
        {
            var layer = context.RhinoDocument.Layers.FindIndex(obj.Attributes.LayerIndex);
            if (!string.IsNullOrWhiteSpace(layerFilter)
                && (layer is null || (!string.Equals(layer.Name, layerFilter, StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(layer.FullPath, layerFilter, StringComparison.OrdinalIgnoreCase))))
            {
                return false;
            }

            if (!string.IsNullOrWhiteSpace(typeFilter)
                && !string.Equals(NativeFunctions.GeometryType(obj.Geometry), typeFilter, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            if (requestedBounds is not null && !Overlaps(obj.Geometry.GetBoundingBox(accurate: true), requestedBounds.Value))
            {
                return false;
            }

            return true;
        }).ToArray();

        return new Dictionary<string, object?>
        {
            ["objects"] = matches.Skip(offset).Take(limit).Select(obj => NativeFunctions.ObjectInfo(obj, includeGeometry)).ToArray(),
            ["total_matching"] = matches.Length,
            ["offset"] = offset,
            ["limit"] = limit,
            ["has_more"] = offset + limit < matches.Length,
            ["filters"] = new Dictionary<string, object?>
            {
                ["layer_filter"] = layerFilter,
                ["type_filter"] = typeFilter,
                ["bbox_filter"] = requestedBounds is null ? null : NativeFunctions.BoundingBox(requestedBounds.Value),
            },
        };
    }

    private static bool Overlaps(BoundingBox first, BoundingBox second) =>
        first.IsValid && second.IsValid
        && first.Min.X <= second.Max.X && first.Max.X >= second.Min.X
        && first.Min.Y <= second.Max.Y && first.Max.Y >= second.Min.Y
        && first.Min.Z <= second.Max.Z && first.Max.Z >= second.Min.Z;
}
