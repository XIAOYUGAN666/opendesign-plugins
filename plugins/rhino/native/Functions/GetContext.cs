using System.Runtime.CompilerServices;
using System.Text.Json;
using Rhino;
using Rhino.Display;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

// Ported from RhinoMCP by Jingcheng Chen, under the MIT license.
internal static class GetContext
{
    private static readonly Guid GrasshopperPluginId = new("b45a29b1-4343-4035-989e-044e8580d9cf");

    [McpCommand("get_context", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Read(RequestContext context, JsonElement parameters)
    {
        var document = context.RhinoDocument;
        var viewport = document.Views.ActiveView?.ActiveViewport
            ?? throw new NativeCommandException("not_found", "No active viewport. Open a model view in Rhino, then retry.");
        var objects = NativeFunctions.Objects(context).ToArray();
        var bounds = BoundingBox.Empty;
        foreach (var obj in objects)
        {
            bounds.Union(obj.Geometry.GetBoundingBox(accurate: true));
        }

        var typeCounts = objects.GroupBy(GetDocumentSummary.NormalizedType)
            .OrderByDescending(group => group.Count())
            .Take(8)
            .ToDictionary(group => group.Key, group => group.Count());
        var selected = document.Objects.GetSelectedObjects(false, false).ToArray();
        var viewportState = ViewportState(viewport);
        viewportState["name"] = viewport.Name;

        return new Dictionary<string, object?>
        {
            ["document_name"] = string.IsNullOrEmpty(document.Name) ? null : document.Name,
            ["document_path"] = null,
            ["units"] = context.ModelUnitSystem.ToString(),
            ["absolute_tolerance"] = context.AbsoluteTolerance,
            ["angle_tolerance"] = document.ModelAngleToleranceDegrees,
            ["current_layer"] = document.Layers.CurrentLayer.FullPath,
            ["layer_count"] = document.Layers.ActiveCount,
            ["object_count"] = objects.Length,
            ["objects_by_type"] = typeCounts,
            ["selection_count"] = selected.Length,
            ["selected_ids"] = selected.Take(20).Select(obj => obj.Id.ToString("D")).ToArray(),
            ["viewport"] = viewportState,
            ["scene_bounding_box"] = SerializeMinMax(bounds),
            ["grasshopper_definition_count"] = CountGrasshopperDefinitions(),
        };
    }

    internal static Dictionary<string, object?> ViewportState(RhinoViewport viewport)
    {
        var up = viewport.CameraUp;
        return new Dictionary<string, object?>
        {
            ["viewport_name"] = viewport.Name,
            ["projection"] = viewport.IsParallelProjection ? "parallel" : "perspective",
            ["display_mode"] = viewport.DisplayMode?.EnglishName ?? "",
            ["camera_location"] = SerializePoint(viewport.CameraLocation),
            ["camera_target"] = SerializePoint(viewport.CameraTarget),
            ["camera_up"] = new[] { Math.Round(up.X, 4), Math.Round(up.Y, 4), Math.Round(up.Z, 4) },
            ["lens_length"] = Math.Round(viewport.Camera35mmLensLength, 2),
        };
    }

    internal static object? SerializeMinMax(BoundingBox box) => box.IsValid
        ? new { min = SerializePoint(box.Min), max = SerializePoint(box.Max) }
        : null;

    private static double[] SerializePoint(Point3d point) =>
        new[] { Math.Round(point.X, 2), Math.Round(point.Y, 2), Math.Round(point.Z, 2) };

    private static int CountGrasshopperDefinitions()
    {
        try
        {
            Rhino.PlugIns.PlugIn.PlugInExists(GrasshopperPluginId, out bool loaded, out _);
            return loaded ? LoadedGrasshopperDocumentCount() : 0;
        }
        catch (Exception)
        {
            return 0;
        }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static int LoadedGrasshopperDocumentCount() =>
        Grasshopper.Instances.DocumentServer?.DocumentCount ?? 0;
}
