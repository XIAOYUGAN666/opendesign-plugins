using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class DescribeCapabilities
{
    [McpCommand("describe_capabilities", ReadOnly = true, SupportsDryRun = false)]
    internal static object Describe(RequestContext context, JsonElement parameters)
    {
        var commands = new CommandRegistry().Commands
            .OrderBy(command => command.Name, StringComparer.Ordinal)
            .Select(command => new Dictionary<string, object?>
            {
                ["name"] = command.Name,
                ["read_only"] = command.ReadOnly,
                ["supports_dry_run"] = command.SupportsDryRun,
            }).ToArray();
        return new Dictionary<string, object?>
        {
            ["version"] = typeof(DescribeCapabilities).Assembly.GetName().Version?.ToString(3)
                ?? throw new InvalidOperationException("Bridge version is unavailable."),
            ["command_count"] = commands.Length,
            ["commands"] = commands,
            ["perception"] = new Dictionary<string, object?>
            {
                ["description"] = "Mutating commands accept opt-in envelope flags.",
                ["envelope_flags"] = new[]
                {
                    new Dictionary<string, object?>
                    {
                        ["flag"] = "include_delta",
                        ["attaches"] = "_delta",
                        ["description"] = "Attaches a change delta to a mutating command result.",
                    },
                    new Dictionary<string, object?>
                    {
                        ["flag"] = "include_health",
                        ["attaches"] = "_health",
                        ["description"] = "Attaches a geometry health report to a mutating command result.",
                    },
                },
            },
        };
    }
}
