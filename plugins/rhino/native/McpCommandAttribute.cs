namespace RhinoMcpBridge;

[AttributeUsage(AttributeTargets.Method, AllowMultiple = false, Inherited = false)]
internal sealed class McpCommandAttribute(string commandName) : Attribute
{
    public string CommandName { get; } =
        string.IsNullOrWhiteSpace(commandName)
            ? throw new ArgumentException("A command name is required.", nameof(commandName))
            : commandName;

    public bool ReadOnly { get; set; }

    public bool SupportsDryRun { get; set; }

    // No further plug-in-owned execution from this handler after its callback returns.
    // This does not guarantee third-party effects have settled or that the command succeeded.
    public bool CompletesWithinCallback { get; set; }
}
