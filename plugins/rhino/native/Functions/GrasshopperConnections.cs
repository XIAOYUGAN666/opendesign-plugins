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
        if (alreadyConnected)
        {
            return ConnectionResult(sourceOwner, source, targetOwner, target, connected: true, alreadyConnected: true);
        }

        return GrasshopperOperations.Mutate(document, "Connect MCP Grasshopper components", () =>
        {
            GrasshopperOperations.RecordWire(document, target, "Connect MCP Grasshopper components");
            target.AddSource(source);
            return ConnectionResult(sourceOwner, source, targetOwner, target, connected: true, alreadyConnected: false);
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
        IGH_DocumentObject? sourceOwner = null;
        if (!disconnectAll)
        {
            sourceOwner = GrasshopperOperations.ResolveSelector(
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
        if (sources.Length == 0)
        {
            return new Dictionary<string, object?>
            {
                ["disconnected_count"] = 0,
                ["disconnected"] = false,
            };
        }

        return GrasshopperOperations.Mutate(document, "Disconnect MCP Grasshopper components", () =>
        {
            GrasshopperOperations.RecordWire(document, target, "Disconnect MCP Grasshopper components");
            foreach (var item in sources)
            {
                target.RemoveSource(item);
            }

            return new Dictionary<string, object?>
            {
                ["disconnected_count"] = sources.Length,
                ["disconnected"] = true,
                ["target_instance_id"] = targetOwner.InstanceGuid.ToString("D"),
                ["source_instance_id"] = sourceOwner?.InstanceGuid.ToString("D"),
            };
        });
    }

    private static Dictionary<string, object?> ConnectionResult(
        IGH_DocumentObject sourceOwner,
        IGH_Param source,
        IGH_DocumentObject targetOwner,
        IGH_Param target,
        bool connected,
        bool alreadyConnected
    ) => new()
    {
        ["source_instance_id"] = sourceOwner.InstanceGuid.ToString("D"),
        ["source_output_name"] = source.NickName,
        ["target_instance_id"] = targetOwner.InstanceGuid.ToString("D"),
        ["target_input_name"] = target.NickName,
        ["connected"] = connected,
        ["already_connected"] = alreadyConnected,
    };
}
