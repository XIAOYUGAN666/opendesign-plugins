using System.Text.Json;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class CreatePlanarRegion
{
    [McpCommand("create_planar_region", ReadOnly = false, SupportsDryRun = false)]
    internal static object Create(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Create MCP planar region", () =>
        {
            var outer = RequireCurve(context, NativeFunctions.Id(parameters, "outer_curve_id"));
            if (!outer.IsClosed || !outer.IsPlanar(context.AbsoluteTolerance))
            {
                throw new NativeCommandException("invalidGeometry", "The outer boundary must be a closed planar curve.");
            }

            var curves = new List<Curve> { outer };
            if (NativeFunctions.Property(parameters, "inner_curve_ids").ValueKind == JsonValueKind.Array)
            {
                foreach (var id in NativeFunctions.Ids(parameters, "inner_curve_ids"))
                {
                    var inner = RequireCurve(context, id);
                    if (!inner.IsClosed || !inner.IsPlanar(context.AbsoluteTolerance))
                    {
                        throw new NativeCommandException("invalidGeometry", "Every inner boundary must be a closed planar curve.");
                    }

                    curves.Add(inner);
                }
            }

            var regions = Brep.CreatePlanarBreps(curves, context.AbsoluteTolerance);
            if (regions is null || regions.Length == 0)
            {
                throw new NativeCommandException("regionCreateFailed", "Rhino could not create the planar region.");
            }

            var ids = new List<string>();
            foreach (var region in regions)
            {
                var attributes = context.RhinoDocument.CreateDefaultAttributes();
                attributes.Name = NativeFunctions.Text(parameters, "name") ?? string.Empty;
                ids.Add(NativeFunctions.AddGeometry(context, region, attributes).ToString("D"));
            }

            return new Dictionary<string, object?>
            {
                ["result_ids"] = ids,
                ["count"] = ids.Count,
                ["message"] = "Planar region created.",
            };
        });

    private static Curve RequireCurve(RequestContext context, Guid id) =>
        NativeFunctions.FindObject(context, id)?.Geometry as Curve
        ?? throw new NativeCommandException("invalidGeometry", "The requested Rhino object is not a curve.");
}
