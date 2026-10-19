using System.Text.Json;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class CreatePlanarRegion
{
    [McpCommand("create_planar_region", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Create(RequestContext context, JsonElement parameters)
    {
        var outer = RequireCurve(context, NativeFunctions.Id(parameters, "outer_curve_id"));
        if (!outer.IsClosed || !outer.TryGetPlane(out var plane, context.AbsoluteTolerance))
        {
            throw new NativeCommandException("invalidGeometry", "The outer boundary must be a closed planar curve.");
        }

        var curves = new List<Curve> { outer };
        if (NativeFunctions.Property(parameters, "inner_curve_ids").ValueKind == JsonValueKind.Array)
        {
            foreach (var id in NativeFunctions.Ids(parameters, "inner_curve_ids"))
            {
                var inner = RequireCurve(context, id);
                if (!inner.IsClosed || !inner.IsInPlane(plane, context.AbsoluteTolerance))
                {
                    throw new NativeCommandException("invalidGeometry", "Every inner boundary must be closed and coplanar with the outer boundary.");
                }

                if (Curve.PlanarClosedCurveRelationship(outer, inner, plane, context.AbsoluteTolerance)
                    != RegionContainment.BInsideA)
                {
                    throw new NativeCommandException("invalidGeometry", "Every inner boundary must be strictly inside the outer boundary.");
                }

                foreach (var previous in curves.Skip(1))
                {
                    if (Curve.PlanarClosedCurveRelationship(previous, inner, plane, context.AbsoluteTolerance)
                        != RegionContainment.Disjoint)
                    {
                        throw new NativeCommandException("invalidGeometry", "Inner boundaries must be distinct, disjoint and nonnested.");
                    }
                }

                curves.Add(inner);
            }
        }

        var regions = Brep.CreatePlanarBreps(curves, context.AbsoluteTolerance);
        if (regions is null || regions.Length == 0)
        {
            throw new NativeCommandException("regionCreateFailed", "Rhino could not create the planar region.");
        }

        if (regions.Length != 1 || regions[0].Faces.Count != 1 || regions[0].Loops.Count != curves.Count)
        {
            foreach (var region in regions)
            {
                region.Dispose();
            }
            throw new NativeCommandException("regionCreateFailed", "Rhino did not create one face with the requested boundaries; no result was committed.");
        }

        using var prepared = regions[0];
        return NativeFunctions.Mutate(context, "Create MCP planar region", () =>
        {
            var attributes = context.RhinoDocument.CreateDefaultAttributes();
            attributes.Name = NativeFunctions.Text(parameters, "name") ?? string.Empty;
            var id = NativeFunctions.AddGeometry(context, prepared, attributes).ToString("D");

            return new Dictionary<string, object?>
            {
                ["result_ids"] = new[] { id },
                ["count"] = 1,
                ["message"] = "Planar region created.",
            };
        });
    }

    private static Curve RequireCurve(RequestContext context, Guid id) =>
        NativeFunctions.FindObject(context, id)?.Geometry as Curve
        ?? throw new NativeCommandException("invalidGeometry", "The requested Rhino object is not a curve.");
}
