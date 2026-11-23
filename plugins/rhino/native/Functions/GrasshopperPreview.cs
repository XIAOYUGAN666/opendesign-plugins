using System.Diagnostics;
using System.Drawing.Imaging;
using System.Text.Json;
#if NET8_0_OR_GREATER
using System.Runtime.Versioning;
#endif
using Grasshopper.Kernel;
using Rhino.Display;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperPreview
{
#if NET8_0_OR_GREATER
    [SupportedOSPlatform("windows6.1")]
#endif
    [McpCommand("gh_capture_preview", ReadOnly = false, SupportsDryRun = false)]
    internal static object Capture(RequestContext context, JsonElement parameters)
    {
        var timer = Stopwatch.StartNew();
        var document = GrasshopperHelpers.RequireDocument(context);
        var width = NativeFunctions.Integer(parameters, "width", 800);
        var height = NativeFunctions.Integer(parameters, "height", 600);
        if (width < 100 || width > 4096 || height < 100 || height > 4096)
        {
            throw new NativeCommandException("invalidParameters", "Preview image dimensions must be between 100 and 4096 pixels.");
        }

        var paddingFactor = NativeFunctions.Number(parameters, "padding_factor", 1.15);
        if (double.IsNaN(paddingFactor) || double.IsInfinity(paddingFactor) || paddingFactor < 1 || paddingFactor > 10)
        {
            throw new NativeCommandException("invalidParameters", "Preview padding must be between 1 and 10.");
        }

        GrasshopperGraphHelpers.OpenCanvas(
            context,
            document,
            NativeFunctions.Boolean(parameters, "open_canvas", true)
        );
        var recompute = NativeFunctions.Boolean(parameters, "recompute", true);
        var solutionTimer = new Stopwatch();
        if (recompute)
        {
            solutionTimer.Start();
            document.NewSolution(false);
            solutionTimer.Stop();
        }

        var targets = GrasshopperGraphHelpers.ReadStringArray(parameters, "targets");
        var graphId = NativeFunctions.Has(parameters, "graph_id")
            ? GrasshopperHelpers.RequireGraphId(parameters)
            : null;
        HashSet<Guid>? targetIds = null;
        if (targets.Length > 0)
        {
            targetIds = targets
                .Select(selector => GrasshopperOperations.ResolveSelector(document, selector, graphId))
                .Select(item => item.InstanceGuid)
                .ToHashSet();
        }
        else if (!string.IsNullOrWhiteSpace(graphId))
        {
            targetIds = GrasshopperHelpers.GraphObjects(document, graphId!)
                .Select(item => item.InstanceGuid)
                .ToHashSet();
        }

        var includeHidden = NativeFunctions.Boolean(parameters, "include_hidden");
        var candidates = document.Objects
            .Where(item => targetIds is null || targetIds.Contains(item.InstanceGuid))
            .Where(item => targets.Length > 0 || item is not Grasshopper.Kernel.Special.GH_Group)
            .ToArray();
        var aliases = graphId is null ? null : GrasshopperHelpers.GraphAliases(document, graphId)
            .GroupBy(entry => entry.Value.InstanceGuid)
            .ToDictionary(group => group.Key, group => group.First().Key);
        var bounds = BoundingBox.Empty;
        var previewObjects = new List<Dictionary<string, object?>>();
        var hiddenSkipped = 0;
        var incapableSkipped = 0;
        var invalidBoundsSkipped = 0;
        foreach (var item in candidates)
        {
            if (item is not IGH_PreviewObject preview)
            {
                incapableSkipped++;
                continue;
            }
            if (!includeHidden && preview.Hidden)
            {
                hiddenSkipped++;
                continue;
            }
            if (!preview.IsPreviewCapable)
            {
                incapableSkipped++;
                continue;
            }
            var clippingBox = preview.ClippingBox;
            if (!clippingBox.IsValid)
            {
                invalidBoundsSkipped++;
                continue;
            }

            bounds.Union(clippingBox);
            var alias = aliases is not null && aliases.TryGetValue(item.InstanceGuid, out var found) ? found : null;
            previewObjects.Add(new Dictionary<string, object?>
            {
                ["instance_id"] = item.InstanceGuid.ToString("D"),
                ["alias"] = alias,
                ["graph_id"] = graphId,
                ["role"] = graphId is null || alias is null ? null : GrasshopperHelpers.GraphRole(document, graphId, alias),
                ["name"] = item.Name,
                ["nickname"] = item.NickName,
                ["bounds"] = DescribeBounds(clippingBox),
            });
        }

        if (!bounds.IsValid || previewObjects.Count == 0)
        {
            throw new NativeCommandException("previewNotFound", "No Grasshopper preview geometry matched the requested scope.");
        }

        var viewportTarget = NormalizeViewport(NativeFunctions.Text(parameters, "viewport", "perspective") ?? "perspective");
        var view = ViewportNavigation.ResolveView(context, viewportTarget == "active" ? null : viewportTarget);
        var viewport = view.ActiveViewport;
        var paddedBounds = Pad(bounds, paddingFactor);
        var showGrid = NativeFunctions.Boolean(parameters, "show_grid", true);
        var showAxes = NativeFunctions.Boolean(parameters, "show_axes", true);
        var showCplaneAxes = NativeFunctions.Boolean(parameters, "show_cplane_axes");
        var pushed = false;
        try
        {
            viewport.PushViewProjection();
            pushed = true;
            if (!viewport.ZoomBoundingBox(paddedBounds))
            {
                throw new NativeCommandException("viewportCaptureFailed", "Rhino could not frame Grasshopper preview geometry.");
            }

            view.Redraw();
            GrasshopperComponents.Redraw(context, document);
            using var bitmap = view.CaptureToBitmap(
                new System.Drawing.Size(width, height), showGrid, showAxes, showCplaneAxes);
            if (bitmap is null)
            {
                throw new NativeCommandException("viewportCaptureFailed", "Rhino did not return a viewport image.");
            }

            using var output = new MemoryStream();
            bitmap.Save(output, ImageFormat.Png);
            return new Dictionary<string, object?>
            {
                ["image_data"] = Convert.ToBase64String(output.ToArray()),
                ["mime_type"] = "image/png",
                ["width"] = bitmap.Width,
                ["height"] = bitmap.Height,
                ["viewport_name"] = viewport.Name,
                ["viewport_target"] = viewportTarget.ToLowerInvariant(),
                ["show_grid"] = showGrid,
                ["show_axes"] = showAxes,
                ["show_cplane_axes"] = showCplaneAxes,
                ["graph_id"] = graphId,
                ["target_count"] = targets.Length,
                ["candidate_count"] = candidates.Length,
                ["captured_preview_object_count"] = previewObjects.Count,
                ["hidden_skipped_count"] = hiddenSkipped,
                ["preview_incapable_skipped_count"] = incapableSkipped,
                ["invalid_bounds_skipped_count"] = invalidBoundsSkipped,
                ["bounds"] = DescribeBounds(bounds),
                ["padded_bounds"] = DescribeBounds(paddedBounds),
                ["padding_factor"] = paddingFactor,
                ["recomputed"] = recompute,
                ["solution_duration_ms"] = solutionTimer.ElapsedMilliseconds,
                ["duration_ms"] = timer.ElapsedMilliseconds,
                ["preview_objects"] = previewObjects,
                ["visibility"] = GrasshopperDocumentCommands.Visibility(context, document, context.GrasshopperCanvas),
            };
        }
        finally
        {
            if (pushed)
            {
                viewport.PopViewProjection();
                view.Redraw();
            }
        }
    }

    private static BoundingBox Pad(BoundingBox bounds, double factor)
    {
        var diagonal = bounds.Diagonal;
        var offset = new Vector3d(
            Math.Max(Math.Abs(diagonal.X) * (factor - 1) / 2, 0.5),
            Math.Max(Math.Abs(diagonal.Y) * (factor - 1) / 2, 0.5),
            Math.Max(Math.Abs(diagonal.Z) * (factor - 1) / 2, 0.5));
        return new BoundingBox(bounds.Min - offset, bounds.Max + offset);
    }

    private static Dictionary<string, object?> DescribeBounds(BoundingBox bounds) => new()
    {
        ["min"] = NativeFunctions.PointArray(bounds.Min),
        ["max"] = NativeFunctions.PointArray(bounds.Max),
        ["center"] = NativeFunctions.PointArray(bounds.Center),
        ["diagonal"] = new[] { bounds.Diagonal.X, bounds.Diagonal.Y, bounds.Diagonal.Z },
    };

    private static string NormalizeViewport(string viewport) => viewport.Trim().ToLowerInvariant() switch
    {
        "active" => "active",
        "perspective" => "Perspective",
        "top" => "Top",
        "front" => "Front",
        "right" => "Right",
        "back" => "Back",
        "left" => "Left",
        "bottom" => "Bottom",
        var custom => custom,
    };
}
