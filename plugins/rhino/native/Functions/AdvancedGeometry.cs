using System.Text.Json;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class AdvancedGeometry
{
    [McpCommand("loft", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Loft(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Loft MCP curves", () =>
        {
            var curves = NativeFunctions.Ids(parameters, "curve_ids").Select(id => RequireCurve(context, id)).ToArray();
            var typeValue = NativeFunctions.Integer(parameters, "loft_type", 0);
            if (typeValue < 0 || typeValue > 4)
            {
                throw new NativeCommandException("invalidParameters", "The loft type is outside the supported range.");
            }

            var surfaces = Brep.CreateFromLoft(curves, Point3d.Unset, Point3d.Unset, (LoftType)typeValue, NativeFunctions.Boolean(parameters, "closed"));
            if (surfaces is null || surfaces.Length == 0)
            {
                throw new NativeCommandException("loftFailed", "Rhino could not loft the input curves.");
            }

            var ids = AddResults(context, surfaces, NativeFunctions.Text(parameters, "name"));
            return new Dictionary<string, object?> { ["result_ids"] = ids, ["count"] = ids.Length, ["message"] = "Loft created." };
        });

    [McpCommand("extrude_curve", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Extrude(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Extrude MCP curve", () =>
        {
            var curve = RequireCurve(context, NativeFunctions.Id(parameters, "curve_id"));
            var direction = NativeFunctions.Vector(NativeFunctions.Property(parameters, "direction"));
            if (!direction.Unitize())
            {
                throw new NativeCommandException("invalidParameters", "The extrusion direction must be non-zero.");
            }

            var height = NativeFunctions.Vector(NativeFunctions.Property(parameters, "direction")).Length;
            if (!curve.TryGetPlane(out var curvePlane, context.AbsoluteTolerance))
            {
                throw new NativeCommandException("invalidGeometry", "Extrusion requires a planar curve.");
            }

            var extrusionPlane = new Plane(curvePlane.Origin, direction);
            var extrusion = Extrusion.Create(curve, extrusionPlane, height, NativeFunctions.Boolean(parameters, "cap", true));
            if (extrusion is null)
            {
                throw new NativeCommandException("extrusionFailed", "Rhino could not extrude the curve.");
            }

            var attributes = context.RhinoDocument.CreateDefaultAttributes();
            attributes.Name = NativeFunctions.Text(parameters, "name") ?? string.Empty;
            var id = NativeFunctions.AddGeometry(context, extrusion, attributes);
            return new Dictionary<string, object?> { ["result_id"] = id.ToString("D"), ["message"] = "Extrusion created." };
        });

    [McpCommand("sweep1", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Sweep(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Sweep MCP curves", () =>
        {
            var rail = RequireCurve(context, NativeFunctions.Id(parameters, "rail_id"));
            var profiles = NativeFunctions.Ids(parameters, "profile_ids").Select(id => RequireCurve(context, id)).ToArray();
            var sweep = new SweepOneRail
            {
                ClosedSweep = NativeFunctions.Boolean(parameters, "closed"),
                SweepTolerance = context.AbsoluteTolerance,
                AngleToleranceRadians = context.AngleToleranceRadians,
            };
            var results = sweep.PerformSweep(rail, profiles);
            if (results is null || results.Length == 0)
            {
                throw new NativeCommandException("sweepFailed", "Rhino could not sweep the profiles along the rail.");
            }

            if (NativeFunctions.Boolean(parameters, "cap_planar_ends"))
            {
                for (var index = 0; index < results.Length; index++)
                {
                    var capped = results[index].CapPlanarHoles(context.AbsoluteTolerance);
                    if (capped is null || !capped.IsSolid)
                    {
                        throw new NativeCommandException("sweepCapFailed", "Every sweep result must cap into a closed solid; no result was committed.");
                    }

                    results[index] = capped;
                }
            }

            var ids = AddResults(context, results, NativeFunctions.Text(parameters, "name"));
            return new Dictionary<string, object?> { ["result_ids"] = ids, ["count"] = ids.Length, ["message"] = "Sweep created." };
        });

    [McpCommand("offset_curve", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Offset(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Offset MCP curve", () =>
        {
            var curve = RequireCurve(context, NativeFunctions.Id(parameters, "curve_id"));
            var normalValue = NativeFunctions.Property(parameters, "plane");
            var plane = normalValue.ValueKind == JsonValueKind.Array
                ? new Plane(curve.PointAtStart, NativeFunctions.Vector(normalValue))
                : curve.TryGetPlane(out var curvePlane, context.AbsoluteTolerance) ? curvePlane : Plane.WorldXY;
            var styleValue = NativeFunctions.Integer(parameters, "corner_style", 1);
            if (styleValue < 0 || styleValue > 4)
            {
                throw new NativeCommandException("invalidParameters", "The curve offset corner style is invalid.");
            }

            var offsets = curve.Offset(plane, NativeFunctions.Number(parameters, "distance", double.NaN), context.AbsoluteTolerance, (CurveOffsetCornerStyle)styleValue);
            if (offsets is null || offsets.Length == 0)
            {
                throw new NativeCommandException("offsetFailed", "Rhino could not offset the curve.");
            }

            var ids = AddResults(context, offsets, NativeFunctions.Text(parameters, "name"));
            return CurveResult(ids, "Offset created.");
        });

    [McpCommand("pipe", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Pipe(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Pipe MCP curve", () =>
        {
            var curve = RequireCurve(context, NativeFunctions.Id(parameters, "curve_id"));
            var radius = NativeFunctions.Number(parameters, "radius", double.NaN);
            if (radius <= 0 || double.IsNaN(radius) || double.IsInfinity(radius))
            {
                throw new NativeCommandException("invalidParameters", "Pipe radius must be a finite positive number.");
            }

            var cap = NativeFunctions.Boolean(parameters, "cap", true) ? PipeCapMode.Flat : PipeCapMode.None;
            var results = Brep.CreatePipe(curve, radius, localBlending: false, cap, NativeFunctions.Boolean(parameters, "fit_rail"), context.AbsoluteTolerance, context.AngleToleranceRadians);
            if (results is null || results.Length == 0)
            {
                throw new NativeCommandException("pipeFailed", "Rhino could not create a pipe along the curve.");
            }

            var ids = AddResults(context, results, NativeFunctions.Text(parameters, "name"));
            return CurveResult(ids, "Pipe created.");
        });

    private static Curve RequireCurve(RequestContext context, Guid id) =>
        NativeFunctions.FindObject(context, id)?.Geometry as Curve
        ?? throw new NativeCommandException("invalidGeometry", "The requested Rhino object is not a curve.");

    private static string[] AddResults(RequestContext context, IEnumerable<GeometryBase> geometry, string? name)
    {
        var ids = new List<string>();
        var index = 0;
        foreach (var item in geometry)
        {
            var attributes = context.RhinoDocument.CreateDefaultAttributes();
            if (!string.IsNullOrWhiteSpace(name))
            {
                attributes.Name = geometry is ICollection<GeometryBase> collection && collection.Count > 1
                    ? $"{name} {++index}"
                    : name;
            }

            ids.Add(NativeFunctions.AddGeometry(context, item, attributes).ToString("D"));
        }

        return ids.ToArray();
    }

    private static Dictionary<string, object?> CurveResult(string[] ids, string message) => new()
    {
        ["result_ids"] = ids,
        ["count"] = ids.Length,
        ["message"] = message,
    };
}
