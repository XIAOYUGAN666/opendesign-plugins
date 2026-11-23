using System.Drawing;
using System.Drawing.Imaging;
using System.Text.Json;
#if NET8_0_OR_GREATER
using System.Runtime.Versioning;
#endif
using Rhino;
using Rhino.Display;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

// Adapted from RhinoMCP's viewport capture and framing implementation.
internal static class CaptureViewport
{
    private sealed record CaptureRequest(
        DisplayModeDescription? Mode, Point3d? Location, Point3d? Target, double? Lens,
        BoundingBox? Frame, bool Zoom, bool Jpeg);

#if NET8_0_OR_GREATER
    [SupportedOSPlatform("windows6.1")]
#endif
    [McpCommand("capture_viewport", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Capture(RequestContext context, JsonElement parameters)
    {
        var width = Math.Max(100, Math.Min(4096, NativeFunctions.Integer(parameters, "width", 800)));
        var height = Math.Max(100, Math.Min(4096, NativeFunctions.Integer(parameters, "height", 600)));
        var grid = NativeFunctions.Boolean(parameters, "show_grid", true);
        var axes = NativeFunctions.Boolean(parameters, "show_axes", true);
        var cplaneAxes = NativeFunctions.Boolean(parameters, "show_cplane_axes");
        var requested = (NativeFunctions.Text(parameters, "viewport", "active") ?? "active").ToLowerInvariant();
        var request = ParseRequest(context, parameters);
        var document = context.RhinoDocument;
        var savedViews = document.Views.Select(view => new
        {
            View = view,
            Projection = new ViewportInfo(view.ActiveViewport),
            view.ActiveViewport.Name,
            Target = view.ActiveViewport.CameraTarget,
            view.ActiveViewport.DisplayMode,
        }).ToArray();

        try
        {
            var view = ResolveView(context, requested);
            var viewport = view.ActiveViewport;
            var name = viewport.Name;
            var location = request.Location ?? viewport.CameraLocation;
            var target = request.Target ?? viewport.CameraTarget;
            if ((request.Location.HasValue || request.Target.HasValue) && location.DistanceTo(target) < RhinoMath.ZeroTolerance)
                throw new NativeCommandException("invalid_argument", "Camera location and target are the same point.");
            if (request.Mode is not null && viewport.DisplayMode?.Id != request.Mode.Id)
                viewport.DisplayMode = request.Mode;

            document.Views.Redraw();
            if (request.Lens.HasValue && viewport.IsPerspectiveProjection)
                viewport.Camera35mmLensLength = request.Lens.Value;
            if (request.Location.HasValue || request.Target.HasValue)
                viewport.SetCameraLocations(request.Target ?? viewport.CameraTarget, request.Location ?? viewport.CameraLocation);

            if (request.Frame is BoundingBox frame)
            {
                var padded = ViewportNavigation.PadForFraming(frame);
                FitProjection(viewport, width, height, fitted => fitted.DollyExtents(padded, 1.1));
            }
            else if (request.Zoom)
            {
                var geometry = VisibleObjects(document, viewport).Select(obj => obj.Geometry).ToArray();
                var bounds = BoundingBox.Empty;
                foreach (var item in geometry)
                    bounds.Union(item.GetBoundingBox(true));
                if (bounds.IsValid)
                {
                    var padded = ViewportNavigation.PadForFraming(bounds);
                    FitProjection(viewport, width, height, fitted => padded.Diagonal != bounds.Diagonal
                        ? fitted.DollyExtents(padded, 1.1)
                        : fitted.DollyExtents(geometry, 1.1));
                }
            }

            using var bitmap = view.CaptureToBitmap(new Size(width, height), grid, axes, cplaneAxes)
                ?? throw new NativeCommandException("viewportCaptureFailed", "Rhino did not return a viewport image.");
            using var output = new MemoryStream();
            bitmap.Save(output, request.Jpeg ? ImageFormat.Jpeg : ImageFormat.Png);

            var objects = NativeFunctions.Objects(context).ToArray();
            var scene = BoundingBox.Empty;
            foreach (var obj in objects)
                scene.Union(obj.Geometry.GetBoundingBox(true));
            var result = GetContext.ViewportState(viewport);
            result["image_data"] = Convert.ToBase64String(output.ToArray());
            result["mime_type"] = request.Jpeg ? "image/jpeg" : "image/png";
            result["viewport_name"] = name;
            result["viewport_target"] = requested;
            result["width"] = bitmap.Width;
            result["height"] = bitmap.Height;
            result["show_grid"] = grid;
            result["show_axes"] = axes;
            result["show_cplane_axes"] = cplaneAxes;
            result["object_count"] = objects.Length;
            result["scene_bounding_box"] = GetContext.SerializeMinMax(scene);
            result["total_object_count"] = objects.Length;
            result["visible_object_count"] = VisibleObjects(document, viewport)
                .Count(obj => viewport.IsVisible(obj.Geometry.GetBoundingBox(true)));
            return result;
        }
        finally
        {
            foreach (var saved in savedViews)
            {
                using (saved.Projection)
                {
                    var viewport = saved.View.ActiveViewport;
                    viewport.SetViewProjection(saved.Projection, false);
                    viewport.SetCameraTarget(saved.Target, false);
                    if (viewport.Name != saved.Name)
                        viewport.Name = saved.Name;
                    if (saved.DisplayMode is not null && viewport.DisplayMode?.Id != saved.DisplayMode.Id)
                        viewport.DisplayMode = saved.DisplayMode;
                }
            }
        }
    }

    private static CaptureRequest ParseRequest(RequestContext context, JsonElement parameters)
    {
        var format = NativeFunctions.Text(parameters, "format", "png")?.ToLowerInvariant();
        if (format is not ("png" or "jpeg"))
            throw new NativeCommandException("invalid_argument", "Pass format png or jpeg.");

        var zoom = NativeFunctions.Boolean(parameters, "zoom_to_fit");
        var framedIds = parameters.TryGetProperty("frame_object_ids", out var ids) && ids.ValueKind == JsonValueKind.Array;
        var framedBox = parameters.TryGetProperty("frame_bbox", out var box) && box.ValueKind == JsonValueKind.Object;
        var lens = ViewportNavigation.Given(parameters, "lens_length")
            ? NativeFunctions.Number(parameters, "lens_length") : (double?)null;
        var location = ReadPoint(parameters, "camera_location");
        var target = ReadPoint(parameters, "camera_target");
        if ((lens.HasValue || location.HasValue || target.HasValue) && (framedIds || framedBox || zoom))
            throw new NativeCommandException("invalid_argument", "An explicit camera cannot be combined with framing.");
        if (lens.HasValue && !(lens > 0))
            throw new NativeCommandException("invalid_argument", "lens_length must be positive.");

        DisplayModeDescription? mode = null;
        var modeName = NativeFunctions.Text(parameters, "display_mode");
        if (modeName is not null)
        {
            var modes = DisplayModeDescription.GetDisplayModes();
            mode = modes.FirstOrDefault(candidate =>
                string.Equals(modeName, candidate.EnglishName, StringComparison.OrdinalIgnoreCase)
                || string.Equals(modeName, candidate.LocalName, StringComparison.OrdinalIgnoreCase));
            if (mode is null)
                throw new NativeCommandException("invalid_argument",
                    $"Display mode '{modeName}' not found. Use one of: {string.Join(", ", modes.Select(item => item.EnglishName))}.");
        }

        BoundingBox? frame = null;
        if (framedIds)
        {
            var bounds = BoundingBox.Empty;
            foreach (var item in ids.EnumerateArray())
            {
                if (!Guid.TryParse(item.GetString(), out var id))
                    throw new NativeCommandException("invalid_argument", "An object ID is not a valid GUID.");
                var obj = NativeFunctions.FindObject(context, id)
                    ?? throw new NativeCommandException("not_found", $"Object '{id}' not found.");
                bounds.Union(obj.Geometry.GetBoundingBox(true));
            }
            frame = bounds;
        }
        else if (framedBox)
        {
            frame = new BoundingBox(NativeFunctions.Point(box.GetProperty("min")), NativeFunctions.Point(box.GetProperty("max")));
        }

        if (frame.HasValue && !frame.Value.IsValid)
            throw new NativeCommandException("invalid_argument", "The framed objects have no valid bounding box. Pass frame_bbox with min <= max.");
        return new CaptureRequest(mode, location, target, lens, frame, zoom, format == "jpeg");
    }

    private static Point3d? ReadPoint(JsonElement parameters, string name) =>
        ViewportNavigation.Given(parameters, name) ? NativeFunctions.Point(parameters.GetProperty(name)) : null;

    private static void FitProjection(RhinoViewport viewport, int width, int height, Func<ViewportInfo, bool> fit)
    {
        using var fitted = new ViewportInfo(viewport);
        fitted.FrustumAspect = (double)width / height;
        if (!fit(fitted) || !viewport.SetViewProjection(fitted, true))
            throw new NativeCommandException("viewportCaptureFailed", "Could not fit geometry to capture dimensions.");
    }

    private static IEnumerable<RhinoObject> VisibleObjects(RhinoDoc document, RhinoViewport viewport) =>
        document.Objects.GetObjectList(new ObjectEnumeratorSettings
        {
            VisibleFilter = true,
            ViewportFilter = viewport,
            NormalObjects = true,
            LockedObjects = true,
        });

    private static RhinoView ResolveView(RequestContext context, string requested)
    {
        var views = context.RhinoDocument.Views;
        if (requested == "active")
            return ViewportNavigation.ResolveView(context, null);
        if (requested == "perspective")
            return views.FirstOrDefault(candidate => candidate.ActiveViewport.IsPerspectiveProjection)
                ?? ViewportNavigation.ResolveView(context, null);

        var named = views.FirstOrDefault(candidate => string.Equals(candidate.ActiveViewport.Name, requested, StringComparison.OrdinalIgnoreCase));
        if (named is not null)
            return named;
        if (requested is not ("top" or "front" or "right" or "back" or "left" or "bottom"))
            return ViewportNavigation.ResolveView(context, requested);
        var projection = (DefinedViewportProjection)Enum.Parse(typeof(DefinedViewportProjection), requested, true);
        var view = ViewportNavigation.ResolveView(context, null);
        view.ActiveViewport.SetProjection(projection, projection.ToString(), false);
        return view;
    }
}
