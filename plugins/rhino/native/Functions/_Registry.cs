using System.Reflection;
using System.Text.Json;

namespace RhinoMcpBridge;

internal delegate object? NativeCommandHandler(RequestContext context, JsonElement parameters);

internal sealed record RegisteredCommand(
    string Name,
    bool ReadOnly,
    bool SupportsDryRun,
    NativeCommandHandler Handler
);

internal sealed class CommandRegistry
{
    private readonly Dictionary<string, RegisteredCommand> _commands;

    public CommandRegistry(Assembly? assembly = null)
    {
        assembly ??= typeof(CommandRegistry).Assembly;
        var entries = new Dictionary<string, RegisteredCommand>(StringComparer.Ordinal);

        foreach (var type in assembly.GetTypes())
        {
            foreach (
                var method in type.GetMethods(
                    BindingFlags.Public
                        | BindingFlags.NonPublic
                        | BindingFlags.Static
                        | BindingFlags.Instance
                )
            )
            {
                var metadata = method.GetCustomAttribute<McpCommandAttribute>(inherit: false);
                if (metadata is null)
                {
                    continue;
                }

                if (!method.IsStatic)
                {
                    throw new InvalidOperationException(
                        $"Native command '{metadata.CommandName}' must be a static handler."
                    );
                }

                var parameters = method.GetParameters();
                if (
                    parameters.Length != 2
                    || parameters[0].ParameterType != typeof(RequestContext)
                    || parameters[1].ParameterType != typeof(JsonElement)
                    || method.ReturnType == typeof(void)
                )
                {
                    throw new InvalidOperationException(
                        $"Native command '{metadata.CommandName}' has an invalid handler signature."
                    );
                }

                var handler = (NativeCommandHandler)
                    method.CreateDelegate(typeof(NativeCommandHandler));
                if (entries.ContainsKey(metadata.CommandName))
                {
                    throw new InvalidOperationException(
                        $"Duplicate native command '{metadata.CommandName}'."
                    );
                }

                entries.Add(
                    metadata.CommandName,
                    new RegisteredCommand(
                        metadata.CommandName,
                        metadata.ReadOnly,
                        metadata.SupportsDryRun,
                        handler
                    )
                );
            }
        }

        _commands = entries;
    }

    public IReadOnlyCollection<RegisteredCommand> Commands => _commands.Values.ToArray();

    public RegisteredCommand Get(string commandName) =>
        _commands.TryGetValue(commandName, out var command)
            ? command
            : throw new NativeCommandException("unsupportedCommand", "The command is not supported.");

    public object? Invoke(string commandName, RequestContext context, JsonElement parameters)
    {
        var command = Get(commandName);
        context.CancellationToken.ThrowIfCancellationRequested();
        return command.Handler(context, parameters);
    }
}

internal sealed class CommandExecutionPipeline(CommandRegistry registry)
{
    private readonly CommandRegistry _registry = registry;

    public object? Execute(string commandName, RequestContext context, JsonElement parameters) =>
        _registry.Invoke(commandName, context, parameters);
}
