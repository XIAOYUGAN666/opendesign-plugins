using System.Text.Json;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class GetDocumentSummary
{
    [McpCommand("get_document_summary", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Get(RequestContext context, JsonElement parameters)
    {
        var objects = NativeFunctions.Objects(context).ToArray();
        var byType = objects.GroupBy(obj => NormalizedType(obj), StringComparer.Ordinal)
            .OrderByDescending(group => group.Count())
            .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);
        var byLayer = objects.GroupBy(obj => context.RhinoDocument.Layers.FindIndex(obj.Attributes.LayerIndex)?.FullPath ?? "(deleted layer)", StringComparer.Ordinal)
            .OrderByDescending(group => group.Count())
            .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);
        var byShortLayer = objects.GroupBy(obj => context.RhinoDocument.Layers.FindIndex(obj.Attributes.LayerIndex)?.Name ?? "(deleted layer)", StringComparer.Ordinal)
            .OrderByDescending(group => group.Count())
            .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);
        var bounds = BoundingBox.Empty;
        foreach (var obj in objects)
        {
            bounds.Union(obj.Geometry.GetBoundingBox(accurate: true));
        }

        var layers = context.RhinoDocument.Layers.Where(layer => !layer.IsDeleted).ToArray();
        var nodes = layers.ToDictionary(layer => layer.Id, layer => new Dictionary<string, object?>
        {
            ["id"] = layer.Id.ToString("D"),
            ["name"] = layer.Name,
            ["full_path"] = layer.FullPath,
            ["parent_id"] = layer.ParentLayerId == Guid.Empty ? null : layer.ParentLayerId.ToString("D"),
            ["color"] = new { r = layer.Color.R, g = layer.Color.G, b = layer.Color.B },
            ["visible"] = layer.IsVisible,
            ["locked"] = layer.IsLocked,
            ["object_count"] = byLayer.TryGetValue(layer.FullPath, out var count) ? count : 0,
            ["children"] = new List<Dictionary<string, object?>>(),
        });
        var hierarchy = new List<Dictionary<string, object?>>();
        foreach (var layer in layers.OrderBy(layer => layer.FullPath, StringComparer.OrdinalIgnoreCase))
        {
            var node = nodes[layer.Id];
            if (nodes.TryGetValue(layer.ParentLayerId, out var parent))
            {
                ((List<Dictionary<string, object?>>)parent["children"]!).Add(node);
            }
            else
            {
                hierarchy.Add(node);
            }
        }

        return new Dictionary<string, object?>
        {
            ["meta_data"] = new Dictionary<string, object?>
            {
                ["name"] = context.RhinoDocument.Name,
                ["units"] = context.ModelUnitSystem.ToString(),
                ["tolerance"] = context.AbsoluteTolerance,
                ["angle_tolerance"] = context.RhinoDocument.ModelAngleToleranceDegrees,
                ["date_created"] = context.RhinoDocument.DateCreated,
                ["date_modified"] = context.RhinoDocument.DateLastEdited,
                ["path"] = null,
            },
            ["object_count"] = objects.Length,
            ["objects_by_type"] = byType,
            ["objects_by_layer"] = byShortLayer,
            ["objects_by_layer_path"] = byLayer,
            ["model_bounding_box"] = bounds.IsValid ? NativeFunctions.BoundingBox(bounds) : null,
            ["layer_count"] = layers.Length,
            ["layer_hierarchy"] = hierarchy,
        };
    }
    internal static string NormalizedType(RhinoObject obj) => obj.Geometry switch
    {
        Rhino.Geometry.Point => "POINT",
        LineCurve => "LINE",
        PolylineCurve => "POLYLINE",
        ArcCurve arc => arc.Arc.IsCircle ? "CIRCLE" : "ARC",
        Curve => "CURVE",
        Extrusion => "EXTRUSION",
        Brep => "BREP",
        Surface => "SURFACE",
        Mesh => "MESH",
        _ => obj.ObjectType.ToString().ToUpperInvariant(),
    };
}
