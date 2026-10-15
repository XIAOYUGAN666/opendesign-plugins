using System.Drawing;
using System.Drawing.Imaging;
using System.Text.Json;
#if NET8_0_OR_GREATER
using System.Runtime.Versioning;
#endif
using Rhino.Display;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class CaptureViewport
{
#if NET8_0_OR_GREATER
    [SupportedOSPlatform("windows6.1")]
#endif
    [McpCommand("capture_viewport", ReadOnly = true, SupportsDryRun = false)]
    internal static object Capture(RequestContext context, JsonElement parameters)
    {
        var width = NativeFunctions.Integer(parameters, "width", 800);
        var height = NativeFunctions.Integer(parameters, "height", 600);
        if (width < 100 || width > 4096 || height < 100 || height > 4096)
        {
            throw new NativeCommandException("invalidParameters", "Viewport image dimensions must be between 100 and 4096 pixels.");
        }

        var requested = NativeFunctions.Text(parameters, "viewport", "active") ?? "active";
        var view = ResolveView(context, requested);
        var viewport = view.ActiveViewport;
        var pushed = false;
        try
        {
            if (NativeFunctions.Boolean(parameters, "zoom_to_fit"))
            {
                viewport.PushViewProjection();
                pushed = true;
                var bounds = BoundingBox.Empty;
                foreach (var obj in NativeFunctions.Objects(context))
                {
                    bounds.Union(obj.Geometry.GetBoundingBox(accurate: true));
                }

                if (bounds.IsValid && !viewport.ZoomBoundingBox(bounds))
                {
                    throw new NativeCommandException("viewportCaptureFailed", "Rhino could not fit the document objects in the requested viewport.");
                }
            }

            using var bitmap = view.CaptureToBitmap(
                new Size(width, height),
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
                ["viewport_target"] = requested,
                ["show_grid"] = NativeFunctions.Boolean(parameters, "show_grid", true),
                ["show_axes"] = NativeFunctions.Boolean(parameters, "show_axes", true),
                ["object_count"] = NativeFunctions.Objects(context).Count(),
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

    internal static RhinoView ResolveView(RequestContext context, string requested)
    {
        var views = context.RhinoDocument.Views.Cast<RhinoView>().ToArray();
        if (string.Equals(requested, "active", StringComparison.OrdinalIgnoreCase))
        {
            return context.RhinoDocument.Views.ActiveView
                ?? throw new NativeCommandException("viewportNotFound", "Rhino has no active viewport.");
        }

        var view = views.FirstOrDefault(candidate => string.Equals(candidate.ActiveViewport.Name, requested, StringComparison.OrdinalIgnoreCase));
        return view ?? throw new NativeCommandException("viewportNotFound", "The requested Rhino viewport was not found.");
    }
}
