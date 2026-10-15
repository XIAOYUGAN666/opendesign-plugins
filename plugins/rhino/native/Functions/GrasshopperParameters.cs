using System.Text.Json;
using Grasshopper.Kernel;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperParameters
{
    [McpCommand("gh_set_parameter_value", ReadOnly = false, SupportsDryRun = false)]
    internal static object SetParameterValue(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        if (!NativeFunctions.Has(parameters, "value"))
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper parameter value is required.");
        }

        var item = GrasshopperHelpers.RequireObject(document, parameters);
        return GrasshopperOperations.Mutate(document, "Set MCP Grasshopper parameter value", () =>
        {
            var value = NativeFunctions.Property(parameters, "value");
            var result = GrasshopperOperations.SetValue(item, value, parameters, document);
            if (item is IGH_ActiveObject active)
            {
                active.ExpireSolution(false);
            }

            return new Dictionary<string, object?>
            {
                ["instance_id"] = item.InstanceGuid.ToString("D"),
                ["value"] = result,
                ["component"] = GrasshopperHelpers.ObjectInfo(item, includeValues: true, maxItems: 100),
            };
        });
    }

    [McpCommand("gh_get_parameter_value", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetParameterValue(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var item = GrasshopperHelpers.RequireObject(document, parameters);
        var parameter = GrasshopperOperations.ResolvePort(
            item,
            parameters,
            "output",
            "output_index",
            "output_name"
        );
        var outputIndex = Array.FindIndex(GrasshopperHelpers.Outputs(item), candidate => ReferenceEquals(candidate, parameter));
        var maxItems = Math.Max(0, Math.Min(1000, NativeFunctions.Integer(parameters, "max_items", 100)));
        return new Dictionary<string, object?>
        {
            ["instance_id"] = item.InstanceGuid.ToString("D"),
            ["output_index"] = outputIndex,
            ["parameter"] = GrasshopperHelpers.PortInfo(parameter, outputIndex, includeValues: true, maxItems),
            ["data"] = GrasshopperHelpers.DataTree(parameter, maxItems),
        };
    }
}
