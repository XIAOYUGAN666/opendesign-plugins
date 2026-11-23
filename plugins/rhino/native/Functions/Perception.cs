using System.Globalization;
using System.Text.Json;
using Rhino.Geometry;
using Rhino.Geometry.Intersect;

namespace RhinoMcpBridge.Functions;

internal static class Perception
{
    [McpCommand("analyze_objects", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Analyze(RequestContext context, JsonElement parameters)
    {
        var objects = ResolveObjects(context, parameters);
        var analyses = objects.Select(obj =>
        {
            var geometry = obj.Geometry;
            var bounds = geometry.GetBoundingBox(accurate: true);
            var metrics = new Dictionary<string, object?>();
            if (geometry is Curve measuredCurve)
            {
                metrics["is_closed"] = measuredCurve.IsClosed;
                metrics["length"] = measuredCurve.GetLength();
                metrics["degree"] = measuredCurve.Degree;
            }

            // Measure lightweight extrusions without replacing their document geometry.
            using var extrusionBrep = (geometry as Extrusion)?.ToBrep(splitKinkyFaces: false);
            var measuredBrep = geometry as Brep ?? extrusionBrep;
            if (measuredBrep is not null)
            {
                metrics["is_solid"] = measuredBrep.IsSolid;
                using (var area = AreaMassProperties.Compute(measuredBrep))
                {
                    metrics["area"] = area?.Area;
                }

                if (measuredBrep.IsSolid)
                {
                    using (var volume = VolumeMassProperties.Compute(measuredBrep))
                    {
                        metrics["volume"] = volume?.Volume;
                    }
                }

                metrics["face_count"] = measuredBrep.Faces.Count;
            }
            else if (geometry is Mesh mesh)
            {
                metrics["vertex_count"] = mesh.Vertices.Count;
                metrics["face_count"] = mesh.Faces.Count;
                metrics["is_closed"] = mesh.IsClosed;
                using (var area = AreaMassProperties.Compute(mesh))
                {
                    metrics["area"] = area?.Area;
                }
                if (mesh.IsClosed)
                {
                    using (var volume = VolumeMassProperties.Compute(mesh))
                    {
                        metrics["volume"] = volume?.Volume;
                    }
                }
            }

            var layer = context.RhinoDocument.Layers.FindIndex(obj.Attributes.LayerIndex);
            return new Dictionary<string, object?>
            {
                ["id"] = obj.Id.ToString("D"),
                ["name"] = obj.Attributes.Name,
                ["type"] = NativeFunctions.GeometryType(geometry),
                ["layer"] = layer?.FullPath ?? string.Empty,
                ["valid"] = geometry.IsValid,
                ["validity_log"] = geometry.IsValid ? null : "RhinoCommon reports invalid geometry.",
                ["bounding_box"] = NativeFunctions.BoundingBox(bounds),
                ["bbox_dimensions"] = bounds.IsValid
                    ? new[] { bounds.Max.X - bounds.Min.X, bounds.Max.Y - bounds.Min.Y, bounds.Max.Z - bounds.Min.Z }
                    : new[] { 0.0, 0.0, 0.0 },
                ["metrics"] = metrics,
            };
        }).ToArray();

        return new Dictionary<string, object?> { ["object_count"] = analyses.Length, ["analyses"] = analyses };
    }

    [McpCommand("measure_objects", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Measure(RequestContext context, JsonElement parameters)
    {
        var ids = NativeFunctions.Ids(parameters, "object_ids");
        var first = NativeFunctions.FindObject(context, ids[0])
            ?? throw new NativeCommandException("objectNotFound", "The first measurement object was not found.");
        var second = NativeFunctions.FindObject(context, ids[1])
            ?? throw new NativeCommandException("objectNotFound", "The second measurement object was not found.");
        var firstBounds = first.Geometry.GetBoundingBox(accurate: true);
        var secondBounds = second.Geometry.GetBoundingBox(accurate: true);
        var bboxGap = BoundingBoxGap(firstBounds, secondBounds);
        var method = "bbox";
        var intersections = 0;
        var clash = bboxGap <= context.AbsoluteTolerance;

        var firstBrep = AsBrep(first.Geometry);
        var secondBrep = AsBrep(second.Geometry);
        if (firstBrep is not null && secondBrep is not null)
        {
            method = "brep";
            Intersection.BrepBrep(firstBrep, secondBrep, context.AbsoluteTolerance, out var curves, out var points);
            intersections = (curves?.Length ?? 0) + (points?.Length ?? 0);
            clash = intersections > 0 || (firstBrep.IsSolid && secondBrep.IsSolid &&
                (Contains(firstBrep, secondBrep, context.AbsoluteTolerance) ||
                 Contains(secondBrep, firstBrep, context.AbsoluteTolerance)));
        }
        else if (first.Geometry is Curve firstCurve && second.Geometry is Curve secondCurve)
        {
            method = "curve";
            var events = Intersection.CurveCurve(firstCurve, secondCurve, context.AbsoluteTolerance, context.AbsoluteTolerance);
            intersections = events?.Count ?? 0;
            clash = intersections > 0;
        }

        return new Dictionary<string, object?>
        {
            ["object_a"] = first.Id.ToString("D"),
            ["object_b"] = second.Id.ToString("D"),
            ["clash"] = clash,
            ["intersection_count"] = intersections,
            ["bbox_gap"] = bboxGap,
            ["method"] = method,
        };
    }

    private static bool Contains(Brep outer, Brep inner, double tolerance) =>
        inner.Vertices.Count > 0 && outer.IsPointInside(inner.Vertices[0].Location, tolerance, strictlyIn: false);

    private static Brep? AsBrep(GeometryBase geometry) => geometry switch
    {
        Brep brep => brep,
        Extrusion extrusion => extrusion.ToBrep(),
        Surface surface => surface.ToBrep(),
        _ => null,
    };

    [McpCommand("section_profile", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Section(RequestContext context, JsonElement parameters)
    {
        var objects = ResolveObjects(context, parameters);
        var profile = NativeFunctions.Property(parameters, "profile");
        if (profile.ValueKind == JsonValueKind.Object)
        {
            return Profile(context, objects, profile);
        }

        var planeValue = NativeFunctions.Property(parameters, "plane");
        var plane = ResolvePlane(planeValue);
        var profiles = objects.Select(obj => SectionObject(context, obj, plane)).ToArray();
        return new Dictionary<string, object?>
        {
            ["mode"] = "plane",
            ["object_count"] = objects.Length,
            ["plane"] = new Dictionary<string, object?>
            {
                ["origin"] = NativeFunctions.PointArray(plane.Origin),
                ["normal"] = new[] { plane.Normal.X, plane.Normal.Y, plane.Normal.Z },
            },
            ["total_section_area"] = profiles.Sum(item => Convert.ToDouble(item["section_area"], CultureInfo.InvariantCulture)),
            ["total_loop_count"] = profiles.Sum(item => (int)item["loop_count"]!),
            ["profiles"] = profiles,
        };
    }

    private static Dictionary<string, object?> Profile(RequestContext context, Rhino.DocObjects.RhinoObject[] objects, JsonElement profile)
    {
        var axis = NativeFunctions.Text(profile, "axis", "Z")!.ToUpperInvariant();
        var count = Math.Max(2, Math.Min(100, NativeFunctions.Integer(profile, "count", 2)));
        var overall = BoundingBox.Empty;
        foreach (var obj in objects)
        {
            overall.Union(obj.Geometry.GetBoundingBox(accurate: true));
        }

        var (minimum, maximum) = axis switch
        {
            "X" => (overall.IsValid ? overall.Min.X : 0, overall.IsValid ? overall.Max.X : 0),
            "Y" => (overall.IsValid ? overall.Min.Y : 0, overall.IsValid ? overall.Max.Y : 0),
            _ => (overall.IsValid ? overall.Min.Z : 0, overall.IsValid ? overall.Max.Z : 0),
        };
        var start = NativeFunctions.Has(profile, "start") ? NativeFunctions.Number(profile, "start") : minimum;
        var end = NativeFunctions.Has(profile, "end") ? NativeFunctions.Number(profile, "end") : maximum;
        if (end < start)
            (start, end) = (end, start);
        var sections = new List<Dictionary<string, object?>>();
        for (var index = 0; index < count; index++)
        {
            var position = start + (end - start) * (index + 0.5) / count;
            var plane = AxisPlane(axis, position);
            var sliced = objects.Select(obj => SectionObject(context, obj, plane)).ToArray();
            sections.Add(new Dictionary<string, object?>
            {
                ["position"] = position,
                ["total_section_area"] = sliced.Sum(item => (double)item["section_area"]!),
                ["loop_count"] = sliced.Sum(item => (int)item["loop_count"]!),
            });
        }

        return new Dictionary<string, object?>
        {
            ["mode"] = "profile",
            ["object_count"] = objects.Length,
            ["axis"] = axis,
            ["count"] = count,
            ["sections"] = sections,
        };
    }

    private static Dictionary<string, object?> SectionObject(RequestContext context, Rhino.DocObjects.RhinoObject obj, Plane plane)
    {
        var curves = SectionCurves(obj.Geometry, plane, context.AbsoluteTolerance);
        var tolerance = context.AbsoluteTolerance;
        var measured = curves.Select(curve => (Curve: curve,
            Area: curve.IsClosed && curve.IsPlanar(tolerance) ? AreaMassProperties.Compute(curve) : null)).ToArray();
        var loops = measured.Select((item, index) =>
        {
            var areaProperties = item.Area;
            var isHole = areaProperties is not null && measured.Where((other, otherIndex) =>
                otherIndex != index && other.Area is not null &&
                Curve.PlanarClosedCurveRelationship(item.Curve, other.Curve, plane, tolerance) == RegionContainment.AInsideB
            ).Count() % 2 == 1;
            return new Dictionary<string, object?>
            {
                ["closed"] = areaProperties is not null,
                ["perimeter"] = item.Curve.GetLength(),
                ["area"] = areaProperties?.Area,
                ["centroid"] = areaProperties is null ? null : NativeFunctions.PointArray(areaProperties.Centroid),
                ["is_hole"] = isHole,
            };
        }).ToArray();
        foreach (var item in measured)
            item.Area?.Dispose();
        var areaSum = loops.Sum(loop => loop["area"] is double area ? (bool)loop["is_hole"]! ? -area : area : 0);
        return new Dictionary<string, object?>
        {
            ["id"] = obj.Id.ToString("D"),
            ["name"] = obj.Attributes.Name,
            ["type"] = NativeFunctions.GeometryType(obj.Geometry),
            ["section_area"] = Math.Max(0, areaSum),
            ["loop_count"] = loops.Length,
            ["loops"] = loops,
        };
    }

    private static Curve[] SectionCurves(GeometryBase geometry, Plane plane, double tolerance)
    {
        var fragments = geometry is Mesh mesh
            ? Mesh.CreateContourCurves(mesh, plane, Rhino.RhinoMath.SqrtEpsilon * 10)
            : AsBrep(geometry) is { } brep ? Brep.CreateContourCurves(brep, plane) : null;
        if (fragments is null || fragments.Length == 0)
            return Array.Empty<Curve>();
        var joined = Curve.JoinCurves(fragments, tolerance);
        return joined is { Length: > 0 } ? joined : fragments;
    }

    private static Plane ResolvePlane(JsonElement value)
    {
        if (NativeFunctions.Has(value, "axis"))
        {
            return AxisPlane(NativeFunctions.Text(value, "axis", "Z")!.ToUpperInvariant(), NativeFunctions.Number(value, "value"));
        }

        var origin = NativeFunctions.Point(NativeFunctions.Property(value, "origin"));
        var normal = NativeFunctions.Vector(NativeFunctions.Property(value, "normal"));
        if (!normal.Unitize())
        {
            throw new NativeCommandException("invalidParameters", "Section plane normal must be non-zero.");
        }

        return new Plane(origin, normal);
    }

    private static Plane AxisPlane(string axis, double position) => axis switch
    {
        "X" => new Plane(new Point3d(position, 0, 0), Vector3d.XAxis),
        "Y" => new Plane(new Point3d(0, position, 0), Vector3d.YAxis),
        "Z" => new Plane(new Point3d(0, 0, position), Vector3d.ZAxis),
        _ => throw new NativeCommandException("invalidParameters", "Section axis must be X, Y, or Z."),
    };

    private static Rhino.DocObjects.RhinoObject[] ResolveObjects(RequestContext context, JsonElement parameters)
    {
        if (NativeFunctions.Has(parameters, "id"))
        {
            return new[] { NativeFunctions.RequireObject(context, parameters) };
        }

        if (NativeFunctions.Has(parameters, "name"))
        {
            return new[] { NativeFunctions.RequireObject(context, parameters) };
        }

        if (NativeFunctions.Has(parameters, "object_ids"))
        {
            return NativeFunctions.Ids(parameters, "object_ids").Select(id => NativeFunctions.FindObject(context, id)
                ?? throw new NativeCommandException("objectNotFound", "A requested Rhino object was not found.")).ToArray();
        }

        if (NativeFunctions.Boolean(parameters, "selected"))
        {
            return NativeFunctions.SelectedIds(context).Select(id => NativeFunctions.FindObject(context, id))
                .Where(obj => obj is not null).Cast<Rhino.DocObjects.RhinoObject>().ToArray();
        }

        throw new NativeCommandException("invalidParameters", "An object selector is required.");
    }

    private static double BoundingBoxGap(BoundingBox first, BoundingBox second)
    {
        if (!first.IsValid || !second.IsValid)
        {
            return 0;
        }

        var dx = Math.Max(0, Math.Max(first.Min.X - second.Max.X, second.Min.X - first.Max.X));
        var dy = Math.Max(0, Math.Max(first.Min.Y - second.Max.Y, second.Min.Y - first.Max.Y));
        var dz = Math.Max(0, Math.Max(first.Min.Z - second.Max.Z, second.Min.Z - first.Max.Z));
        return Math.Sqrt(dx * dx + dy * dy + dz * dz);
    }
}
