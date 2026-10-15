using System.Text.Json;
using Rhino;

namespace RhinoMcpBridge.Functions;

internal static class RunCommand
{
    [McpCommand("run_command", ReadOnly = false, SupportsDryRun = false)]
    internal static object Run(RequestContext context, JsonElement parameters) =>
        NativeFunctions.Mutate(context, "Run MCP Rhino command", () =>
        {
            var commandText = NativeFunctions.Text(parameters, "command");
            if (commandText is null || string.IsNullOrWhiteSpace(commandText) || commandText.Length > 2048 || commandText.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0)
            {
                throw new NativeCommandException("invalidParameters", "The Rhino command text is invalid.");
            }

            var command = commandText.Trim();

            var before = RhinoApp.CommandHistoryWindowText ?? string.Empty;
            var success = RhinoApp.RunScript(context.RhinoDocument.RuntimeSerialNumber, command, NativeFunctions.Boolean(parameters, "echo"));
            var after = RhinoApp.CommandHistoryWindowText ?? string.Empty;
            var output = after.StartsWith(before, StringComparison.Ordinal)
                ? after.Substring(before.Length).Trim()
                : after.Trim();
            return new Dictionary<string, object?>
            {
                ["success"] = success,
                ["output"] = output,
                ["message"] = success ? "Rhino command completed." : "Rhino command did not complete successfully.",
            };
        });
}
