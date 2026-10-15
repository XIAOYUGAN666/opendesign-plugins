using System.Text.Json;
using Rhino.Commands;

namespace RhinoMcpBridge.Functions;

internal static class GetCommands
{
    [McpCommand("get_commands", ReadOnly = true, SupportsDryRun = false)]
    internal static object Get(RequestContext context, JsonElement parameters)
    {
        var loadedOnly = NativeFunctions.Boolean(parameters, "loaded_only", true);
        var filter = NativeFunctions.Text(parameters, "filter")?.Trim();
        var commands = Command.GetCommandNames(english: true, loaded: loadedOnly)
            .Where(name => !string.IsNullOrWhiteSpace(name))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(name => name, StringComparer.OrdinalIgnoreCase);
        if (!string.IsNullOrEmpty(filter))
        {
            commands = commands.Where(name => name.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0)
                .OrderBy(name => name, StringComparer.OrdinalIgnoreCase);
        }

        var result = commands.ToArray();
        return new Dictionary<string, object?>
        {
            ["commands"] = result,
            ["count"] = result.Length,
            ["loaded_only"] = loadedOnly,
            ["filter"] = filter,
        };
    }
}
