using System.Globalization;
using System.Text.Json;
using Color = System.Drawing.Color;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class NativeFunctions
{
    internal static JsonElement Property(JsonElement value, string name) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var property)
            ? property
            : default;

    internal static bool Has(JsonElement value, string name) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out _);

    internal static string? Text(JsonElement value, string name, string? fallback = null)
    {
        var property = Property(value, name);
        return property.ValueKind == JsonValueKind.String ? property.GetString() : fallback;
    }

    internal static double Number(JsonElement value, string name, double fallback = 0)
    {
        var property = Property(value, name);
        return property.ValueKind == JsonValueKind.Number && property.TryGetDouble(out var number)
            ? number
            : fallback;
    }

    internal static int Integer(JsonElement value, string name, int fallback = 0)
    {
        var property = Property(value, name);
        return property.ValueKind == JsonValueKind.Number && property.TryGetInt32(out var number)
            ? number
            : fallback;
    }

    internal static bool Boolean(JsonElement value, string name, bool fallback = false)
    {
        var property = Property(value, name);
        return property.ValueKind is JsonValueKind.True or JsonValueKind.False
            ? property.GetBoolean()
            : fallback;
    }

    internal static Point3d Point(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Object)
        {
            return new Point3d(
                Number(value, "x", double.NaN),
                Number(value, "y", double.NaN),
                Number(value, "z", double.NaN)
            );
        }

        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() != 3)
        {
            throw new NativeCommandException("invalidParameters", "A 3D point must contain three coordinates.");
        }

        var coordinates = value.EnumerateArray().Select(ReadFiniteNumber).ToArray();
        return new Point3d(coordinates[0], coordinates[1], coordinates[2]);
    }

    internal static Vector3d Vector(JsonElement value)
    {
        var point = Point(value);
        var vector = new Vector3d(point.X, point.Y, point.Z);
        if (!vector.IsValid)
        {
            throw new NativeCommandException("invalidParameters", "A 3D vector is invalid.");
        }

        return vector;
    }

    internal static Point3d[] Points(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Array)
        {
            throw new NativeCommandException("invalidParameters", "Points must be an array.");
        }

        return value.EnumerateArray().Select(Point).ToArray();
    }

    internal static double ReadFiniteNumber(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetDouble(out var number) || double.IsNaN(number) || double.IsInfinity(number))
        {
            throw new NativeCommandException("invalidParameters", "A numeric parameter is invalid.");
        }

        return number;
    }

    internal static Guid Id(JsonElement value, string key)
    {
        var text = Text(value, key);
        if (!Guid.TryParse(text, out var id))
        {
            throw new NativeCommandException("invalidParameters", "An object identifier is invalid.");
        }

        return id;
    }

    internal static Guid[] Ids(JsonElement value, string key)
    {
        var ids = Property(value, key);
        if (ids.ValueKind != JsonValueKind.Array)
        {
            throw new NativeCommandException("invalidParameters", "Object identifiers are invalid.");
        }

        return ids.EnumerateArray().Select(item =>
        {
            if (item.ValueKind != JsonValueKind.String || !Guid.TryParse(item.GetString(), out var id))
            {
                throw new NativeCommandException("invalidParameters", "An object identifier is invalid.");
            }

            return id;
        }).ToArray();
    }

    internal static RhinoObject RequireObject(RequestContext context, JsonElement parameters, string idKey = "id", string nameKey = "name")
    {
        var doc = context.RhinoDocument;
        if (Has(parameters, idKey))
        {
            var obj = doc.Objects.FindId(Id(parameters, idKey));
            return obj ?? throw new NativeCommandException("objectNotFound", "The Rhino object was not found.");
        }

        var name = Text(parameters, nameKey);
        if (string.IsNullOrWhiteSpace(name))
        {
            throw new NativeCommandException("invalidParameters", "An object identifier or name is required.");
        }

        var matches = doc.Objects
            .Where(item => item is not null && !item.IsDeleted && string.Equals(item.Attributes.Name, name, StringComparison.OrdinalIgnoreCase))
            .Take(2)
            .ToArray();
        return matches.Length switch
        {
            1 => matches[0],
            0 => throw new NativeCommandException("objectNotFound", "The Rhino object was not found."),
            _ => throw new NativeCommandException("ambiguousObjectName", "The object name is not unique; use its identifier."),
        };
    }

    internal static RhinoObject? FindObject(RequestContext context, Guid id) =>
        context.RhinoDocument.Objects.FindId(id);

    internal static IEnumerable<RhinoObject> Objects(RequestContext context) =>
        context.RhinoDocument.Objects.Where(item => item is not null && !item.IsDeleted);

    internal static ObjectAttributes Attributes(RequestContext context, JsonElement parameters, string nameKey = "name", string colorKey = "color")
    {
        var attributes = context.RhinoDocument.CreateDefaultAttributes();
        var name = Text(parameters, nameKey);
        if (!string.IsNullOrWhiteSpace(name))
        {
            attributes.Name = name;
        }

        if (TryColor(parameters, colorKey, out var color))
        {
            attributes.ColorSource = ObjectColorSource.ColorFromObject;
            attributes.ObjectColor = color;
        }

        return attributes;
    }

    internal static bool TryColor(JsonElement parameters, string key, out Color color)
    {
        var value = Property(parameters, key);
        if (value.ValueKind != JsonValueKind.Array)
        {
            color = Color.Empty;
            return false;
        }

        var channels = value.EnumerateArray().Select(ReadFiniteNumber).ToArray();
        if (channels.Length != 3 || channels.Any(channel => channel < 0 || channel > 255 || Math.Truncate(channel) != channel))
        {
            throw new NativeCommandException("invalidParameters", "RGB color channels must be integers from 0 to 255.");
        }

        color = Color.FromArgb((int)channels[0], (int)channels[1], (int)channels[2]);
        return true;
    }

    internal static Guid AddGeometry(RequestContext context, GeometryBase geometry, ObjectAttributes attributes)
    {
        var id = context.RhinoDocument.Objects.Add(geometry, attributes);
        if (id == Guid.Empty)
        {
            throw new NativeCommandException("geometryAddFailed", "Rhino could not add the geometry to the document.");
        }

        return id;
    }

    internal static T Mutate<T>(RequestContext context, string description, Func<T> action)
    {
        var doc = context.RhinoDocument;
        uint ownedRecord = 0;
        uint nextRecord = 0;
        var ownedRecordUndone = false;
        var undoMarker = new object();
        return UndoTransaction.Execute(
            context.CancellationToken,
            description,
            action,
            label =>
            {
                ownedRecord = doc.BeginUndoRecord(label);
                nextRecord = doc.NextUndoRecordSerialNumber;
                return ownedRecord;
            },
            serial =>
            {
                if (doc.CurrentUndoRecordSerialNumber != serial
                    || doc.NextUndoRecordSerialNumber != nextRecord)
                {
                    return false;
                }
                var closed = doc.EndUndoRecord(serial);
                // EndRecording callbacks may synchronously create another record.
                return closed && doc.CurrentUndoRecordSerialNumber == 0
                    && doc.NextUndoRecordSerialNumber == nextRecord;
            },
            // Keep a private event even when validation makes no document changes.
            // Its callback must confirm that rollback targeted this record.
            () => doc.CurrentUndoRecordSerialNumber == ownedRecord
                && doc.AddCustomUndoEvent(description, (_, e) =>
                {
                    if (e.Document.RuntimeSerialNumber == doc.RuntimeSerialNumber
                        && ReferenceEquals(e.Tag, undoMarker))
                    {
                        ownedRecordUndone = true;
                    }
                }, undoMarker),
            () => doc.CurrentUndoRecordSerialNumber == 0
                && doc.NextUndoRecordSerialNumber == nextRecord
                && UndoRedo.ApplyStep(doc, undo: true) && ownedRecordUndone,
            () => doc.Views.Redraw()
        );
    }

    internal static Dictionary<string, object?> ObjectInfo(RhinoObject obj, bool includeGeometry = true)
    {
        var geometry = obj.Geometry;
        var bbox = geometry.GetBoundingBox(accurate: true);
        var info = new Dictionary<string, object?>
        {
            ["id"] = obj.Id.ToString("D"),
            ["name"] = string.IsNullOrWhiteSpace(obj.Attributes.Name) ? "(unnamed)" : obj.Attributes.Name,
            ["type"] = GeometryType(geometry),
            ["layer"] = obj.Document?.Layers.FindIndex(obj.Attributes.LayerIndex)?.FullPath ?? "(deleted layer)",
            ["color"] = RgbObject(obj.Attributes.ObjectColor),
            ["bounding_box"] = BoundingBox(bbox),
            ["is_valid"] = geometry.IsValid,
        };
        if (includeGeometry)
        {
            var detail = GeometryInfo(geometry);
            if (detail is not null)
            {
                info["geometry"] = detail;
            }
        }

        return info;
    }

    internal static string GeometryType(GeometryBase geometry) => geometry switch
    {
        Rhino.Geometry.Point => "POINT",
        Curve => "CURVE",
        Brep => "BREP",
        Extrusion => "EXTRUSION",
        Mesh => "MESH",
        SubD => "SUBD",
        PointCloud => "POINT_CLOUD",
        _ => geometry.ObjectType.ToString().ToUpperInvariant(),
    };

    internal static object BoundingBox(BoundingBox bbox) => bbox.IsValid
        ? new[]
        {
            new[] { bbox.Min.X, bbox.Min.Y, bbox.Min.Z },
            new[] { bbox.Max.X, bbox.Max.Y, bbox.Max.Z },
        }
        : Array.Empty<double[]>();

    internal static object? GeometryInfo(GeometryBase geometry)
    {
        if (geometry is Point point)
        {
            return new Dictionary<string, object?>
            {
                ["location"] = PointArray(point.Location),
            };
        }

        if (geometry is Curve curve)
        {
            var domain = curve.Domain;
            return new Dictionary<string, object?>
            {
                ["degree"] = curve.Degree,
                ["is_closed"] = curve.IsClosed,
                ["length"] = curve.GetLength(),
                ["start"] = PointArray(curve.PointAtStart),
                ["end"] = PointArray(curve.PointAtEnd),
                ["domain"] = new[] { domain.Min, domain.Max },
            };
        }

        if (geometry is Brep brep)
        {
            return new Dictionary<string, object?>
            {
                ["face_count"] = brep.Faces.Count,
                ["edge_count"] = brep.Edges.Count,
                ["is_solid"] = brep.IsSolid,
                ["volume"] = brep.IsSolid ? VolumeMassProperties.Compute(brep)?.Volume : null,
                ["area"] = AreaMassProperties.Compute(brep)?.Area,
            };
        }

        return null;
    }

    internal static double[] PointArray(Point3d point) => new[] { point.X, point.Y, point.Z };

    internal static int[] Rgb(Color color) => new[] { (int)color.R, (int)color.G, (int)color.B };

    internal static Dictionary<string, int> RgbObject(Color color) => new()
    {
        ["r"] = color.R,
        ["g"] = color.G,
        ["b"] = color.B,
    };

    internal static IEnumerable<Guid> SelectedIds(RequestContext context) =>
        context.RhinoDocument.Objects
            .GetSelectedObjects(includeLights: false, includeGrips: false)
            .Where(item => item is not null)
            .Select(item => item!.Id);

    internal static GeometryBase CreateGeometry(RequestContext context, string type, JsonElement parameters)
    {
        var center = Property(parameters, "center");
        var origin = center.ValueKind == JsonValueKind.Array ? Point(center) : Point3d.Origin;
        var radius = Number(parameters, "radius", double.NaN);
        return type.ToUpperInvariant() switch
        {
            "POINT" => new Rhino.Geometry.Point(new Point3d(Number(parameters, "x", double.NaN), Number(parameters, "y", double.NaN), Number(parameters, "z", double.NaN))),
            "LINE" => new LineCurve(Point(Property(parameters, "start")), Point(Property(parameters, "end"))),
            "POLYLINE" => new PolylineCurve(Points(Property(parameters, "points"))),
            "CIRCLE" => new ArcCurve(new Circle(Plane.WorldXY, origin, radius)),
            "ARC" => new ArcCurve(new Arc(new Circle(Plane.WorldXY, origin, radius), Number(parameters, "angle", double.NaN) * Math.PI / 180.0)),
            "ELLIPSE" => new Ellipse(new Plane(origin, Vector3d.ZAxis), Number(parameters, "radius_x", double.NaN), Number(parameters, "radius_y", double.NaN)).ToNurbsCurve(),
            "CURVE" => Curve.CreateInterpolatedCurve(Points(Property(parameters, "points")), Integer(parameters, "degree", 3))
                ?? throw new NativeCommandException("geometryCreateFailed", "Rhino could not interpolate the requested curve."),
            "BOX" => Brep.CreateFromBox(new BoundingBox(
                new Point3d(-Number(parameters, "width", double.NaN) / 2, -Number(parameters, "length", double.NaN) / 2, -Number(parameters, "height", double.NaN) / 2),
                new Point3d(Number(parameters, "width", double.NaN) / 2, Number(parameters, "length", double.NaN) / 2, Number(parameters, "height", double.NaN) / 2)
            )),
            "SPHERE" => new Sphere(new Plane(origin, Vector3d.ZAxis), radius).ToBrep(),
            "CONE" => new Cone(new Plane(Point3d.Origin, Vector3d.ZAxis), Number(parameters, "height", double.NaN), radius).ToBrep(Boolean(parameters, "cap", true)),
            "CYLINDER" => new Cylinder(new Circle(Plane.WorldXY, radius), Number(parameters, "height", double.NaN)).ToBrep(Boolean(parameters, "cap", true), Boolean(parameters, "cap", true)),
            "SURFACE" => CreateSurface(parameters),
            _ => throw new NativeCommandException("unsupportedGeometry", "The requested geometry type is not supported."),
        };
    }

    internal static GeometryBase CreateSurface(JsonElement parameters)
    {
        var count = Property(parameters, "count");
        if (count.ValueKind != JsonValueKind.Array || count.GetArrayLength() != 2)
        {
            throw new NativeCommandException("invalidParameters", "Surface point count must contain two integers.");
        }

        var counts = count.EnumerateArray().Select(value => value.GetInt32()).ToArray();
        var degreeValue = Property(parameters, "degree");
        var degrees = degreeValue.ValueKind == JsonValueKind.Array
            ? degreeValue.EnumerateArray().Select(value => value.GetInt32()).ToArray()
            : new[] { Math.Min(3, counts[0] - 1), Math.Min(3, counts[1] - 1) };
        var closedValue = Property(parameters, "closed");
        var closed = closedValue.ValueKind == JsonValueKind.Array
            ? closedValue.EnumerateArray().Select(value => value.GetBoolean()).ToArray()
            : new[] { false, false };
        var surface = NurbsSurface.CreateThroughPoints(
            Points(Property(parameters, "points")), counts[0], counts[1], degrees[0], degrees[1], closed[0], closed[1]
        );
        return surface ?? throw new NativeCommandException("geometryCreateFailed", "Rhino could not create the requested surface.");
    }

    internal static void ApplyTransforms(GeometryBase geometry, JsonElement parameters, Point3d rotationOrigin)
    {
        var scale = Property(parameters, "scale");
        if (scale.ValueKind == JsonValueKind.Array)
        {
            var factor = Vector(scale);
            if (factor.X == 0 || factor.Y == 0 || factor.Z == 0)
            {
                throw new NativeCommandException("invalidParameters", "Scale factors must be non-zero.");
            }

            geometry.Transform(Transform.Scale(new Plane(rotationOrigin, Vector3d.ZAxis), factor.X, factor.Y, factor.Z));
        }

        var rotation = Property(parameters, "rotation");
        if (rotation.ValueKind == JsonValueKind.Array)
        {
            var angles = Vector(rotation);
            geometry.Transform(Transform.Rotation(angles.Z, Vector3d.ZAxis, rotationOrigin));
            geometry.Transform(Transform.Rotation(angles.Y, Vector3d.YAxis, rotationOrigin));
            geometry.Transform(Transform.Rotation(angles.X, Vector3d.XAxis, rotationOrigin));
        }

        var translation = Property(parameters, "translation");
        if (translation.ValueKind == JsonValueKind.Array)
        {
            geometry.Transform(Transform.Translation(Vector(translation)));
        }
    }

    internal static void ApplyModifyTransforms(GeometryBase geometry, JsonElement parameters, BoundingBox originalBounds)
    {
        var scale = Property(parameters, "scale");
        if (scale.ValueKind == JsonValueKind.Array)
        {
            var factor = Vector(scale);
            if (factor.X == 0 || factor.Y == 0 || factor.Z == 0)
            {
                throw new NativeCommandException("invalidParameters", "Scale factors must be non-zero.");
            }

            geometry.Transform(Transform.Scale(new Plane(originalBounds.Min, Vector3d.ZAxis), factor.X, factor.Y, factor.Z));
        }

        var rotation = Property(parameters, "rotation");
        if (rotation.ValueKind == JsonValueKind.Array)
        {
            var angles = Vector(rotation);
            var center = originalBounds.Center;
            geometry.Transform(Transform.Rotation(angles.Z, Vector3d.ZAxis, center));
            geometry.Transform(Transform.Rotation(angles.Y, Vector3d.YAxis, center));
            geometry.Transform(Transform.Rotation(angles.X, Vector3d.XAxis, center));
        }

        var translation = Property(parameters, "translation");
        if (translation.ValueKind == JsonValueKind.Array)
        {
            geometry.Transform(Transform.Translation(Vector(translation)));
        }
    }

    internal static Layer RequireLayer(RequestContext context, JsonElement parameters, string guidKey = "guid", string nameKey = "name")
    {
        var layers = context.RhinoDocument.Layers;
        if (Has(parameters, guidKey))
        {
            var idText = Text(parameters, guidKey);
            var layer = Guid.TryParse(idText, out var id) ? layers.FirstOrDefault(item => item.Id == id && !item.IsDeleted) : null;
            return layer ?? throw new NativeCommandException("layerNotFound", "The Rhino layer was not found.");
        }

        var name = Text(parameters, nameKey);
        if (string.IsNullOrWhiteSpace(name))
        {
            throw new NativeCommandException("invalidParameters", "A layer identifier or name is required.");
        }

        var matches = layers.Where(item => !item.IsDeleted && (string.Equals(item.FullPath, name, StringComparison.OrdinalIgnoreCase) || string.Equals(item.Name, name, StringComparison.OrdinalIgnoreCase))).Take(2).ToArray();
        return matches.Length switch
        {
            1 => matches[0],
            0 => throw new NativeCommandException("layerNotFound", "The Rhino layer was not found."),
            _ => throw new NativeCommandException("ambiguousLayerName", "The layer name is not unique; use its full path or identifier."),
        };
    }

    internal static Dictionary<string, object?> LayerInfo(Layer layer)
    {
        var info = new Dictionary<string, object?>
        {
            ["id"] = layer.Id.ToString("D"),
            ["name"] = layer.Name,
            ["color"] = RgbObject(layer.Color),
            ["visible"] = layer.IsVisible,
            ["locked"] = layer.IsLocked,
        };
        if (layer.ParentLayerId != Guid.Empty)
        {
            info["parent"] = layer.ParentLayerId.ToString("D");
        }

        return info;
    }

    internal static double ParseAngleTolerance(RequestContext context) => context.AngleToleranceRadians;
}

internal static class UndoTransaction
{
    internal static T Execute<T>(
        CancellationToken cancellationToken,
        string description,
        Func<T> action,
        Func<string, uint> beginUndoRecord,
        Func<uint, bool> endUndoRecord,
        Func<bool> recordOwnership,
        Func<bool> undo,
        Action redraw
    )
    {
        cancellationToken.ThrowIfCancellationRequested();
        var serial = beginUndoRecord(description);
        if (serial == 0)
        {
            throw new NativeCommandException(
                "undoUnavailable",
                "Rhino could not start a dedicated undo record; the mutation was not applied."
            );
        }

        var closeAttempted = false;
        var undoRecordClosed = false;
        bool CloseOwnedRecord()
        {
            if (!closeAttempted)
            {
                closeAttempted = true;
                try
                {
                    undoRecordClosed = endUndoRecord(serial);
                }
                catch
                {
                    undoRecordClosed = false;
                }
            }
            return undoRecordClosed;
        }
        void CloseBeforeAction()
        {
            if (!CloseOwnedRecord())
            {
                throw new NativeCommandException(
                    "undoRecordFailed",
                    "Rhino could not confirm closure of the undo record; no mutation was started."
                );
            }
        }
        bool owned;
        try
        {
            owned = recordOwnership();
        }
        catch
        {
            // No action ran. Never roll back an empty or unowned record.
            CloseBeforeAction();
            throw;
        }
        if (!owned)
        {
            CloseBeforeAction();
            throw new NativeCommandException(
                "undoUnavailable",
                "Rhino could not establish a dedicated undo event; the mutation was not applied."
            );
        }
        try
        {
            var result = action();
            undoRecordClosed = CloseOwnedRecord();
            if (!undoRecordClosed)
            {
                throw new NativeCommandException(
                    "undoRecordFailed",
                    "Rhino could not close the undo record for this mutation."
                );
            }

            return result;
        }
        catch (Exception)
        {
            try
            {
                var rollbackReady = CloseOwnedRecord();
                if (!rollbackReady || !undo())
                {
                    throw new InvalidOperationException();
                }
            }
            catch
            {
                throw new NativeCommandException(
                    "rollbackFailed",
                    "The Rhino mutation failed and rollback could not be confirmed."
                );
            }

            throw;
        }
        finally
        {
            redraw();
        }
    }
}
