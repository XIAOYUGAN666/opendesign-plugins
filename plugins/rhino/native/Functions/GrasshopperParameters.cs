using System.Text.Json;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Data;
using Grasshopper.Kernel.Special;
using Grasshopper.Kernel.Types;

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
        if (!GrasshopperReadback.HasData(parameter) && item is IGH_ActiveObject active)
        {
            if (item is IGH_Component component)
            {
                foreach (var slider in component.Params.Input
                    .SelectMany(port => port.Sources).OfType<GH_NumberSlider>())
                {
                    if (GrasshopperReadback.HasData(slider))
                        continue;
                    slider.ClearData();
                    slider.AddVolatileData(new GH_Path(0), 0, new GH_Number((double)slider.CurrentValue));
                }
            }

            active.ClearData();
            active.CollectData();
            active.ComputeData();
            if (!GrasshopperReadback.HasData(parameter))
            {
                active.ExpireSolution(true);
                GrasshopperOperations.Recompute(document, expireAll: false);
            }
        }

        var maxItems = Math.Max(0, Math.Min(10000, NativeFunctions.Integer(parameters, "max_items", 100)));
        var result = GrasshopperReadback.ParameterData(parameter, maxItems);
        result["instance_id"] = item.InstanceGuid.ToString("D");
        result["nickname"] = item.NickName;
        result["param_name"] = parameter.Name;
        result["type_name"] = GrasshopperHelpers.SafeParamTypeName(parameter);
        return result;
    }
}
