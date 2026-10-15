using System.Text.Json;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class GetDocumentSummary
{
    [McpCommand("get_document_summary", ReadOnly = true, SupportsDryRun = false)]
    internal static object Get(RequestContext context, JsonElement parameters)
    {
        var objects = NativeFunctions.Objects(context).ToArray();
        var byType = objects.GroupBy(obj => NativeFunctions.GeometryType(obj.Geometry), StringComparer.Ordinal)
            .OrderBy(group => group.Key, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);
        var byLayer = objects.GroupBy(obj => context.RhinoDocument.Layers.FindIndex(obj.Attributes.LayerIndex)?.FullPath ?? "(deleted layer)", StringComparer.Ordinal)
            .OrderBy(group => group.Key, StringComparer.Ordinal)
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
                ["angle_tolerance"] = context.AngleToleranceRadians,
            },
            ["object_count"] = objects.Length,
            ["objects_by_type"] = byType,
            ["objects_by_layer"] = byLayer,
            ["model_bounding_box"] = bounds.IsValid ? NativeFunctions.BoundingBox(bounds) : null,
            ["layer_count"] = layers.Length,
            ["layer_hierarchy"] = hierarchy,
        };
    }
}
