using System.Text.Json;
using Grasshopper.Kernel;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperConnections
{
    [McpCommand("gh_connect_components", ReadOnly = false, SupportsDryRun = false)]
    internal static object ConnectComponents(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var sourceOwner = GrasshopperOperations.ResolveSelector(
            document,
            parameters,
            "source_instance_id",
            "source_nickname"
        );
        var targetOwner = GrasshopperOperations.ResolveSelector(
            document,
            parameters,
            "target_instance_id",
            "target_nickname"
        );
        var source = GrasshopperOperations.ResolvePort(
            sourceOwner,
            parameters,
            "output",
            "source_output_index",
            "source_output_name"
        );
        var target = GrasshopperOperations.ResolvePort(
            targetOwner,
            parameters,
            "input",
            "target_input_index",
            "target_input_name"
        );
        if (ReferenceEquals(source, target))
        {
            throw new NativeCommandException("invalidConnection", "A Grasshopper parameter cannot connect to itself.");
        }

        var alreadyConnected = target.Sources.Any(item => ReferenceEquals(item, source));
        return GrasshopperOperations.Mutate(document, "Connect MCP Grasshopper components", () =>
        {
            if (!alreadyConnected)
            {
                GrasshopperOperations.RecordWire(document, target, "Connect MCP Grasshopper components");
                target.AddSource(source);
            }
            GrasshopperParameters.EnsureData(source);
            targetOwner.ExpireSolution(true);
            GrasshopperOperations.Recompute(document, expireAll: false);
            return new Dictionary<string, object?>
            {
                ["source_id"] = sourceOwner.InstanceGuid.ToString("D"),
                ["source_nickname"] = sourceOwner.NickName,
                ["source_param"] = source.Name,
                ["target_id"] = targetOwner.InstanceGuid.ToString("D"),
                ["target_nickname"] = targetOwner.NickName,
                ["target_param"] = target.Name,
                ["message"] = $"Connected {sourceOwner.NickName}.{source.Name} to {targetOwner.NickName}.{target.Name}",
            };
        });
    }

    [McpCommand("gh_disconnect_components", ReadOnly = false, SupportsDryRun = false)]
    internal static object DisconnectComponents(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var targetOwner = GrasshopperOperations.ResolveSelector(
            document,
            parameters,
            "target_instance_id",
            "target_nickname"
        );
        var target = GrasshopperOperations.ResolvePort(
            targetOwner,
            parameters,
            "input",
            "target_input_index",
            "target_input_name"
        );
        var disconnectAll = NativeFunctions.Boolean(parameters, "disconnect_all");
        IGH_Param? source = null;
        if (!disconnectAll)
        {
            var sourceOwner = GrasshopperOperations.ResolveSelector(
                document,
                parameters,
                "source_instance_id",
                "source_nickname"
            );
            source = GrasshopperOperations.ResolvePort(
                sourceOwner,
                parameters,
                "output",
                "source_output_index",
                "source_output_name"
            );
        }

        var sources = disconnectAll
            ? target.Sources.ToArray()
            : target.Sources.Where(item => ReferenceEquals(item, source)).Take(1).ToArray();
        return GrasshopperOperations.Mutate(document, "Disconnect MCP Grasshopper components", () =>
        {
            if (sources.Length > 0)
                GrasshopperOperations.RecordWire(document, target, "Disconnect MCP Grasshopper components");
            foreach (var item in sources)
            {
                target.RemoveSource(item);
            }

            targetOwner.ExpireSolution(true);
            GrasshopperOperations.Recompute(document, expireAll: false);
            return new Dictionary<string, object?>
            {
                ["disconnected_count"] = sources.Length,
                ["target_id"] = targetOwner.InstanceGuid.ToString("D"),
                ["target_nickname"] = targetOwner.NickName,
                ["target_param"] = target.Name,
                ["message"] = sources.Length > 0
                    ? $"Disconnected {sources.Length} connection(s) from {targetOwner.NickName}.{target.Name}"
                    : "No connections were disconnected",
            };
        });
    }
}
