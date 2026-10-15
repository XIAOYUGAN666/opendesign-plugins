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

        var canvasOpened = GrasshopperGraphHelpers.OpenCanvas(
            context,
            document,
            NativeFunctions.Boolean(parameters, "open_canvas", true)
        );
        if (NativeFunctions.Boolean(parameters, "recompute", true))
        {
            document.NewSolution(false);
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
        var previewObjects = document.Objects
            .Where(item => targetIds is null || targetIds.Contains(item.InstanceGuid))
            .OfType<IGH_PreviewObject>()
            .Where(item => includeHidden || !item.Hidden)
            .ToArray();
        var bounds = BoundingBox.Empty;
        var included = 0;
        foreach (var preview in previewObjects)
        {
            var clippingBox = preview.ClippingBox;
            if (!clippingBox.IsValid)
            {
                continue;
            }

            bounds.Union(clippingBox);
            included++;
        }

        if (!bounds.IsValid || included == 0)
        {
            throw new NativeCommandException("previewNotFound", "No Grasshopper preview geometry matched the requested scope.");
        }

        var viewportTarget = NormalizeViewport(NativeFunctions.Text(parameters, "viewport", "perspective") ?? "perspective");
        var view = CaptureViewport.ResolveView(context, viewportTarget);
        var viewport = view.ActiveViewport;
        var pushed = false;
        try
        {
            viewport.PushViewProjection();
            pushed = true;
            bounds = Pad(bounds, paddingFactor);
            if (!viewport.ZoomBoundingBox(bounds))
            {
                throw new NativeCommandException("viewportCaptureFailed", "Rhino could not frame Grasshopper preview geometry.");
            }

            using var bitmap = view.CaptureToBitmap(
                new System.Drawing.Size(width, height),
                NativeFunctions.Boolean(parameters, "show_grid", true),
                NativeFunctions.Boolean(parameters, "show_axes", true),
                NativeFunctions.Boolean(parameters, "show_cplane_axes")
            );
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
                ["viewport_target"] = viewportTarget,
                ["captured_preview_object_count"] = included,
                ["preview_bounds"] = NativeFunctions.BoundingBox(bounds),
                ["canvas_opened"] = canvasOpened,
                ["recomputed"] = NativeFunctions.Boolean(parameters, "recompute", true),
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
        if (factor <= 1)
        {
            return bounds;
        }

        var diagonal = bounds.Diagonal.Length;
        var padding = diagonal > 1e-9 ? diagonal * (factor - 1) * 0.5 : factor - 1;
        var offset = new Vector3d(padding, padding, padding);
        return new BoundingBox(bounds.Min - offset, bounds.Max + offset);
    }

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
