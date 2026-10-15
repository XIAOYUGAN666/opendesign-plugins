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
}
