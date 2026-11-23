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
            var currentValue = GrasshopperOperations.SetValue(item, value, parameters, document);
            item.ExpireSolution(true);
            if (item is GH_NumberSlider slider)
            {
                EnsureData(slider);
                foreach (var recipient in slider.Recipients)
                    (recipient.Attributes?.GetTopLevel.DocObject ?? recipient).ExpireSolution(true);
            }
            GrasshopperOperations.Recompute(document, expireAll: false);
            var result = new Dictionary<string, object?>
            {
                ["instance_id"] = item.InstanceGuid.ToString("D"),
                ["nickname"] = item.NickName,
            };
            switch (item)
            {
                case GH_NumberSlider number:
                    result["value"] = (double)number.CurrentValue;
                    result["min"] = (double)number.Slider.Minimum;
                    result["max"] = (double)number.Slider.Maximum;
                    result["message"] = $"Set slider '{item.NickName}' to {number.CurrentValue}";
                    break;
                case GH_BooleanToggle:
                    result["value"] = currentValue;
                    result["message"] = $"Set toggle '{item.NickName}' to {currentValue}";
                    break;
                case GH_Panel:
                    result["value"] = currentValue;
                    result["message"] = $"Set panel '{item.NickName}' content";
                    break;
                case GH_ValueList:
                    result["message"] = $"Set value list '{item.NickName}' selection";
                    break;
                default:
                    var port = GrasshopperOperations.ResolvePort(item, parameters, "input", "input_index", "input_name");
                    result["param_name"] = port.Name;
                    result["message"] = $"Set value on {item.NickName}.{port.Name}";
                    break;
            }
            return result;
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
                foreach (var source in component.Params.Input.SelectMany(port => port.Sources))
                    EnsureData(source);
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

    internal static void EnsureData(IGH_Param parameter)
    {
        if (parameter is not GH_NumberSlider slider || GrasshopperReadback.HasData(slider))
            return;
        slider.ClearData();
        slider.AddVolatileData(new GH_Path(0), 0, new GH_Number((double)slider.CurrentValue));
    }
}
