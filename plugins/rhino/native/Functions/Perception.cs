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

            if (geometry is Brep measuredBrep)
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

        var firstBrep = first.Geometry as Brep ?? (first.Geometry as Extrusion)?.ToBrep();
        var secondBrep = second.Geometry as Brep ?? (second.Geometry as Extrusion)?.ToBrep();
        if (firstBrep is not null && secondBrep is not null)
        {
            method = "brep";
            if (Intersection.BrepBrep(firstBrep, secondBrep, context.AbsoluteTolerance, out var curves, out var points))
            {
                intersections = (curves?.Length ?? 0) + (points?.Length ?? 0);
                clash = intersections > 0;
            }
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
        var sections = new List<Dictionary<string, object?>>();
        for (var index = 0; index < count; index++)
        {
            var position = start + (end - start) * index / (count - 1);
            var plane = AxisPlane(axis, position);
            var curves = objects.SelectMany(obj => SectionCurves(obj.Geometry, plane, context.AbsoluteTolerance)).ToArray();
            sections.Add(new Dictionary<string, object?>
            {
                ["position"] = position,
                ["total_section_area"] = curves.Where(curve => curve.IsClosed).Sum(curve => AreaMassProperties.Compute(curve)?.Area ?? 0),
                ["loop_count"] = curves.Length,
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
        var loops = curves.Select(curve =>
        {
            var areaProperties = curve.IsClosed ? AreaMassProperties.Compute(curve) : null;
            var orientation = curve.IsClosed ? curve.ClosedCurveOrientation(plane) : CurveOrientation.Undefined;
            var isHole = orientation == CurveOrientation.Clockwise;
            var area = areaProperties?.Area;
            var centroid = areaProperties is null ? null : NativeFunctions.PointArray(areaProperties.Centroid);
            areaProperties?.Dispose();
            return new Dictionary<string, object?>
            {
                ["closed"] = curve.IsClosed,
                ["perimeter"] = curve.GetLength(),
                ["area"] = area,
                ["centroid"] = centroid,
                ["is_hole"] = isHole,
            };
        }).ToArray();
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
        var brep = geometry as Brep ?? (geometry as Extrusion)?.ToBrep();
        if (brep is not null)
        {
            return Intersection.BrepPlane(brep, plane, tolerance, out var curves, out _) && curves is not null
                ? curves
                : Array.Empty<Curve>();
        }

        if (geometry is Mesh mesh)
        {
            return Mesh.CreateContourCurves(mesh, plane, tolerance) ?? Array.Empty<Curve>();
        }

        if (geometry is Curve curve)
        {
            var point = Intersection.CurvePlane(curve, plane, tolerance);
            return point is null ? Array.Empty<Curve>() : Array.Empty<Curve>();
        }

        return Array.Empty<Curve>();
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
