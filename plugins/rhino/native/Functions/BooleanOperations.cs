using System.Text.Json;
using Rhino;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class BooleanOperations
{
    [McpCommand("boolean_union", ReadOnly = false, SupportsDryRun = true, CompletesWithinCallback = true)]
    internal static object Union(RequestContext context, JsonElement parameters) => Execute(context, parameters, "boolean_union");

    [McpCommand("boolean_difference", ReadOnly = false, SupportsDryRun = true, CompletesWithinCallback = true)]
    internal static object Difference(RequestContext context, JsonElement parameters) => Execute(context, parameters, "boolean_difference");

    [McpCommand("boolean_intersection", ReadOnly = false, SupportsDryRun = true, CompletesWithinCallback = true)]
    internal static object Intersection(RequestContext context, JsonElement parameters) => Execute(context, parameters, "boolean_intersection");

    private static object Execute(RequestContext context, JsonElement parameters, string operation)
    {
        var isDifference = operation == "boolean_difference";
        var ids = isDifference
            ? new[] { NativeFunctions.Id(parameters, "base_id") }.Concat(NativeFunctions.Ids(parameters, "subtract_ids")).ToArray()
            : NativeFunctions.Ids(parameters, "object_ids");
        var objects = ids.Select(id => NativeFunctions.FindObject(context, id)
                ?? throw new NativeCommandException("objectNotFound", "A Boolean input object was not found."))
            .ToArray();
        var breps = objects.Select(obj => obj.Geometry as Brep ?? (obj.Geometry as Extrusion)?.ToBrep())
            .ToArray();
        if (breps.Any(brep => brep is null || !brep.IsSolid))
        {
            throw new NativeCommandException("invalidBooleanInput", "Boolean operations require valid closed solid Breps or extrusions.");
        }

        var result = Calculate(operation, breps!, context.AbsoluteTolerance);
        var preview = NativeFunctions.Boolean(parameters, "dry_run");
        if (preview)
        {
            var metrics = result.Select(brep =>
            {
                var volume = VolumeMassProperties.Compute(brep)?.Volume;
                var area = AreaMassProperties.Compute(brep)?.Area;
                return new Dictionary<string, object?>
                {
                    ["valid"] = brep.IsValid,
                    ["is_solid"] = brep.IsSolid,
                    ["volume"] = volume,
                    ["area"] = area,
                    ["bounding_box"] = NativeFunctions.BoundingBox(brep.GetBoundingBox(accurate: true)),
                };
            }).ToArray();
            return new Dictionary<string, object?>
            {
                ["dry_run"] = true,
                ["would_succeed"] = result.Length > 0 && result.All(brep => brep.IsValid && brep.IsSolid),
                ["count"] = result.Length,
                ["results"] = metrics,
                ["message"] = result.Length > 0 ? "Boolean preview computed without changing the document." : "The Boolean operation produced no result.",
            };
        }

        var outputName = NativeFunctions.Text(parameters, "name");
        var deleteSources = NativeFunctions.Boolean(parameters, "delete_sources", true);
        return NativeFunctions.Mutate(context, "Boolean MCP geometry", () =>
        {
            var resultIds = new List<string>();
            for (var index = 0; index < result.Length; index++)
            {
                var attributes = context.RhinoDocument.CreateDefaultAttributes();
                if (!string.IsNullOrWhiteSpace(outputName))
                {
                    attributes.Name = result.Length == 1 ? outputName : $"{outputName} {index + 1}";
                }

                resultIds.Add(NativeFunctions.AddGeometry(context, result[index], attributes).ToString("D"));
            }

            if (deleteSources)
            {
                foreach (var input in objects)
                {
                    if (!context.RhinoDocument.Objects.Delete(input.Id, quiet: true))
                    {
                        throw new NativeCommandException("booleanCommitFailed", "Rhino could not remove the Boolean source objects.");
                    }
                }
            }

            return new Dictionary<string, object?>
            {
                ["result_ids"] = resultIds,
                ["count"] = resultIds.Count,
                ["message"] = $"Boolean operation created {resultIds.Count} result object(s).",
            };
        });
    }

    private static Brep[] Calculate(string operation, Brep[] breps, double tolerance)
    {
        Brep[]? result;
        if (operation == "boolean_union")
        {
            result = Brep.CreateBooleanUnion(breps, tolerance);
        }
        else if (operation == "boolean_difference")
        {
            result = Brep.CreateBooleanDifference(new[] { breps[0] }, breps.Skip(1), tolerance);
        }
        else
        {
            result = new[] { breps[0] };
            foreach (var next in breps.Skip(1))
            {
                result = result.SelectMany(current => Brep.CreateBooleanIntersection(current, next, tolerance) ?? Array.Empty<Brep>()).ToArray();
                if (result.Length == 0)
                {
                    break;
                }
            }
        }

        return result ?? Array.Empty<Brep>();
    }
}
