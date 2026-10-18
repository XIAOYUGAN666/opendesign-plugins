using System.Text.Json;
using Rhino.Geometry;
using Rhino.Geometry.Intersect;

namespace RhinoMcpBridge.Functions;

internal static class CurveOperations
{
    [McpCommand("project_curve", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Project(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Project MCP curve", () =>
        {
            var curve = RequireCurve(context, NativeFunctions.Id(parameters, "curve_id"));
            var targets = NativeFunctions.Ids(parameters, "target_ids")
                .Select(id => NativeFunctions.FindObject(context, id)
                    ?? throw new NativeCommandException("objectNotFound", "A projection target was not found."))
                .ToArray();
            var direction = NativeFunctions.Vector(NativeFunctions.Property(parameters, "direction"));
            if (!direction.Unitize())
            {
                throw new NativeCommandException("invalidParameters", "Projection direction must be non-zero.");
            }

            var results = new List<Curve>();
            var breps = targets.Select(obj => obj.Geometry as Brep ?? (obj.Geometry as Extrusion)?.ToBrep())
                .Where(brep => brep is not null).Cast<Brep>().ToArray();
            var meshes = targets.Select(obj => obj.Geometry as Mesh).Where(mesh => mesh is not null).Cast<Mesh>().ToArray();
            if (breps.Length > 0)
            {
                results.AddRange(Curve.ProjectToBrep(curve, breps, direction, context.AbsoluteTolerance) ?? Array.Empty<Curve>());
            }

            foreach (var mesh in meshes)
            {
                results.AddRange(Curve.ProjectToMesh(curve, mesh, direction, context.AbsoluteTolerance) ?? Array.Empty<Curve>());
            }

            if (results.Count == 0)
            {
                throw new NativeCommandException("projectionFailed", "The curve did not project onto the requested targets.");
            }

            var ids = AddCurves(context, results, NativeFunctions.Text(parameters, "name"));
            return new Dictionary<string, object?> { ["result_ids"] = ids, ["count"] = ids.Length, ["message"] = "Curve projected." };
        });

    [McpCommand("intersect_curves", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Intersect(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Intersect MCP curves", () =>
        {
            var first = RequireCurve(context, NativeFunctions.Id(parameters, "curve_id_a"));
            var second = RequireCurve(context, NativeFunctions.Id(parameters, "curve_id_b"));
            var tolerance = NativeFunctions.Number(parameters, "tolerance", context.AbsoluteTolerance);
            if (tolerance <= 0 || double.IsNaN(tolerance) || double.IsInfinity(tolerance))
            {
                throw new NativeCommandException("invalidParameters", "Intersection tolerance must be finite and positive.");
            }

            var events = Intersection.CurveCurve(first, second, tolerance, tolerance);
            if (events is null)
            {
                throw new NativeCommandException("intersectionFailed", "Rhino could not compute curve intersections.");
            }

            var pointIds = new List<string>();
            var curveIds = new List<string>();
            var points = new List<double[]>();
            var name = NativeFunctions.Text(parameters, "name");
            foreach (var intersection in events)
            {
                if (intersection.IsPoint)
                {
                    pointIds.Add(context.RhinoDocument.Objects.AddPoint(intersection.PointA).ToString("D"));
                    points.Add(NativeFunctions.PointArray(intersection.PointA));
                }
                else if (intersection.IsOverlap)
                {
                    var overlap = first.Trim(intersection.OverlapA);
                    if (overlap is not null)
                    {
                        var attributes = context.RhinoDocument.CreateDefaultAttributes();
                        attributes.Name = name ?? string.Empty;
                        curveIds.Add(NativeFunctions.AddGeometry(context, overlap, attributes).ToString("D"));
                    }
                }
            }

            return new Dictionary<string, object?>
            {
                ["point_ids"] = pointIds,
                ["curve_ids"] = curveIds,
                ["points"] = points,
                ["count"] = pointIds.Count + curveIds.Count,
                ["message"] = "Curve intersections computed.",
            };
        });

    [McpCommand("split_curve", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Split(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Split MCP curve", () =>
        {
            var curveObject = NativeFunctions.FindObject(context, NativeFunctions.Id(parameters, "curve_id"))
                ?? throw new NativeCommandException("objectNotFound", "The requested curve was not found.");
            var curve = curveObject.Geometry as Curve
                ?? throw new NativeCommandException("invalidGeometry", "The requested Rhino object is not a curve.");
            var splitParameters = new List<double>();
            var values = NativeFunctions.Property(parameters, "parameters");
            if (values.ValueKind == JsonValueKind.Array)
            {
                splitParameters.AddRange(values.EnumerateArray().Select(NativeFunctions.ReadFiniteNumber));
            }

            var points = NativeFunctions.Property(parameters, "point_ids");
            if (points.ValueKind == JsonValueKind.Array)
            {
                foreach (var pointId in NativeFunctions.Ids(parameters, "point_ids"))
                {
                    var pointObject = NativeFunctions.FindObject(context, pointId);
                    var location = pointObject?.Geometry is Rhino.Geometry.Point point
                        ? point.Location
                        : pointObject?.Geometry is PointCloud cloud && cloud.Count > 0
                            ? cloud[0].Location
                            : throw new NativeCommandException("invalidGeometry", "Each split point must reference a Rhino point object." );
                    if (!curve.ClosestPoint(location, out var parameter))
                    {
                        throw new NativeCommandException("splitFailed", "Rhino could not map a split point onto the curve.");
                    }

                    splitParameters.Add(parameter);
                }
            }

            var split = curve.Split(splitParameters.Distinct().OrderBy(value => value).ToArray());
            if (split is null || split.Length == 0)
            {
                throw new NativeCommandException("splitFailed", "The curve did not split at the requested parameters.");
            }

            var ids = AddCurves(context, split, NativeFunctions.Text(parameters, "name"));
            if (NativeFunctions.Boolean(parameters, "delete_source", true)
                && !context.RhinoDocument.Objects.Delete(curveObject.Id, quiet: true))
            {
                throw new NativeCommandException("splitCommitFailed", "Rhino could not remove the original curve.");
            }

            return new Dictionary<string, object?> { ["result_ids"] = ids, ["count"] = ids.Length, ["message"] = "Curve split." };
        });

    private static Curve RequireCurve(RequestContext context, Guid id) =>
        NativeFunctions.FindObject(context, id)?.Geometry as Curve
        ?? throw new NativeCommandException("invalidGeometry", "The requested Rhino object is not a curve.");

    private static string[] AddCurves(RequestContext context, IEnumerable<Curve> curves, string? name)
    {
        var results = curves.ToArray();
        var ids = new string[results.Length];
        for (var index = 0; index < results.Length; index++)
        {
            var attributes = context.RhinoDocument.CreateDefaultAttributes();
            if (!string.IsNullOrWhiteSpace(name))
            {
                attributes.Name = results.Length > 1 ? $"{name} {index + 1}" : name;
            }

            ids[index] = NativeFunctions.AddGeometry(context, results[index], attributes).ToString("D");
        }

        return ids;
    }
}
