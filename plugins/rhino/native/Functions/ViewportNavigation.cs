using System.Text.Json;
using Rhino;
using Rhino.Display;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

// Adapted from RhinoMCP's SetCamera.cs, ZoomTo.cs and _shared.cs.
internal static class ViewportNavigation
{
    [McpCommand("set_camera", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object SetCamera(RequestContext context, JsonElement parameters)
    {
        var locationGiven = Given(parameters, "location");
        var targetGiven = Given(parameters, "target");
        var upGiven = Given(parameters, "up");
        var lensGiven = Given(parameters, "lens_length");
        var projection = NativeFunctions.Text(parameters, "projection");

        if (!locationGiven && !targetGiven && !upGiven && !lensGiven && string.IsNullOrEmpty(projection))
            throw new NativeCommandException("invalid_argument",
                "set_camera needs at least one of location, target, up, lens_length or projection.");

        var view = ResolveView(context, NativeFunctions.Text(parameters, "viewport"));
        var viewport = view.ActiveViewport;
        var location = locationGiven ? NativeFunctions.Point(parameters.GetProperty("location")) : viewport.CameraLocation;
        var target = targetGiven ? NativeFunctions.Point(parameters.GetProperty("target")) : viewport.CameraTarget;

        if (projection is not (null or "" or "parallel" or "perspective"))
            throw new NativeCommandException("invalid_argument", "Pass projection perspective or parallel.");
        if ((locationGiven || targetGiven) && location.DistanceTo(target) < RhinoMath.ZeroTolerance)
            throw new NativeCommandException("invalid_argument", "Camera location and target are the same point.");

        var direction = target - location;
        var upPoint = upGiven ? NativeFunctions.Point(parameters.GetProperty("up")) : Point3d.Origin;
        var up = new Vector3d(upPoint.X, upPoint.Y, upPoint.Z);
        if (upGiven && (up.IsTiny() || up.IsParallelTo(direction) != 0))
            throw new NativeCommandException("invalid_argument", "Camera up vector is zero or parallel to the view direction.");

        var lens = NativeFunctions.Number(parameters, "lens_length");
        if (lensGiven && !(lens > 0))
            throw new NativeCommandException("invalid_argument", "lens_length must be positive.");

        if (projection == "parallel" && !viewport.IsParallelProjection)
            viewport.ChangeToParallelProjection(true);
        else if (projection == "perspective" && !viewport.IsPerspectiveProjection)
            viewport.ChangeToPerspectiveProjection(true, viewport.Camera35mmLensLength);
        if (locationGiven || targetGiven)
            viewport.SetCameraLocations(target, location);
        if (upGiven)
            viewport.CameraUp = up;
        if (lensGiven)
            viewport.Camera35mmLensLength = lens;

        view.Redraw();
        return GetContext.ViewportState(viewport);
    }

    [McpCommand("zoom_to", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object ZoomTo(RequestContext context, JsonElement parameters)
    {
        var covered = ZoomTargets(context, parameters).ToArray();
        var view = ResolveView(context, NativeFunctions.Text(parameters, "viewport"));
        var bounds = BoundingBox.Empty;
        foreach (var obj in covered)
            bounds.Union(obj.Geometry.GetBoundingBox(true));
        if (!bounds.IsValid)
            throw new NativeCommandException("invalid_argument", "Nothing to zoom to: the target covers no visible objects.");

        var viewport = view.ActiveViewport;
        viewport.ZoomBoundingBox(PadForFraming(bounds));
        view.Redraw();
        var state = GetContext.ViewportState(viewport);
        state["bounding_box"] = GetContext.SerializeMinMax(bounds);
        state["object_count"] = covered.Length;
        return state;
    }

    private static IEnumerable<RhinoObject> ZoomTargets(RequestContext context, JsonElement parameters)
    {
        var document = context.RhinoDocument;
        switch (NativeFunctions.Text(parameters, "target"))
        {
            case "all":
                return document.Objects.GetObjectList(new ObjectEnumeratorSettings
                {
                    VisibleFilter = true,
                    NormalObjects = true,
                    LockedObjects = true,
                });
            case "selection":
                var selected = document.Objects.GetSelectedObjects(false, false).ToArray();
                if (selected.Length == 0)
                    throw new NativeCommandException("invalid_argument", "Nothing is selected to zoom to.");
                return selected;
            case "object_ids":
                if (!Given(parameters, "object_ids"))
                    throw new NativeCommandException("invalid_argument", "target object_ids needs object_ids.");
                return parameters.GetProperty("object_ids").EnumerateArray().Select(item =>
                {
                    if (!Guid.TryParse(item.GetString(), out var id))
                        throw new NativeCommandException("invalid_argument", "An object ID is not a valid GUID.");
                    return NativeFunctions.FindObject(context, id)
                        ?? throw new NativeCommandException("not_found", $"Object '{id}' not found.");
                }).ToArray();
            case "layer":
                var name = NativeFunctions.Text(parameters, "layer");
                if (string.IsNullOrEmpty(name))
                    throw new NativeCommandException("invalid_argument", "target layer needs layer.");
                var layer = document.Layers.FirstOrDefault(item => !item.IsDeleted &&
                    string.Equals(item.FullPath, name, StringComparison.OrdinalIgnoreCase))
                    ?? document.Layers.FirstOrDefault(item => !item.IsDeleted &&
                        string.Equals(item.Name, name, StringComparison.OrdinalIgnoreCase))
                    ?? throw new NativeCommandException("not_found", $"Layer '{name}' not found.");
                return (document.Objects.FindByLayer(layer) ?? Array.Empty<RhinoObject>()).Where(obj => obj.Visible);
            default:
                throw new NativeCommandException("invalid_argument", "Pass target all, selection, object_ids or layer.");
        }
    }

    internal static RhinoView ResolveView(RequestContext context, string? name)
    {
        var views = context.RhinoDocument.Views;
        if (string.IsNullOrEmpty(name))
            return views.ActiveView ?? throw new NativeCommandException("not_found", "No active viewport.");
        return views.FirstOrDefault(view => string.Equals(view.ActiveViewport.Name, name, StringComparison.OrdinalIgnoreCase))
            ?? throw new NativeCommandException("not_found", $"Viewport '{name}' not found.");
    }

    internal static bool Given(JsonElement parameters, string name) =>
        parameters.TryGetProperty(name, out var value) && value.ValueKind switch
        {
            JsonValueKind.Null => false,
            JsonValueKind.String => !string.IsNullOrEmpty(value.GetString()),
            JsonValueKind.Array => value.GetArrayLength() > 0,
            _ => true,
        };

    internal static BoundingBox PadForFraming(BoundingBox bounds)
    {
        var diagonal = bounds.Diagonal;
        var largest = Math.Max(diagonal.X, Math.Max(diagonal.Y, diagonal.Z));
        var pad = largest < RhinoMath.SqrtEpsilon ? 1.0 : largest * 0.05;
        bounds.Inflate(
            diagonal.X < RhinoMath.SqrtEpsilon ? pad : 0,
            diagonal.Y < RhinoMath.SqrtEpsilon ? pad : 0,
            diagonal.Z < RhinoMath.SqrtEpsilon ? pad : 0);
        return bounds;
    }
}
