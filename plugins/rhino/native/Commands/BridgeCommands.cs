using Rhino;
using Rhino.Commands;
using Rhino.Input;
using Rhino.Input.Custom;

namespace RhinoMcpBridge.Commands;

internal static class BridgeCommandActions
{
    internal static (bool Success, string Output) Start(int port)
    {
        var controller = RhinoMcpBridgePlugin.Instance?.Controller;
        if (controller is null)
            return (false, "RhinoMCP server not started: Bridge is not loaded.");
        if (port is < 0 or > 65535)
            return (false, $"{port} is not a valid port.");
        var success = controller.StartEndpoint(port, out var output);
        return (success, output);
    }

    internal static string Stop() => RhinoMcpBridgePlugin.Instance?.Controller?.StopEndpoint()
        ?? "RhinoMCP server is not running.";

    internal static string Version() =>
        $"RhinoMCPPlugin version {typeof(BridgeCommandActions).Assembly.GetName().Version}";
}

public sealed class MCPStartCommand : Command
{
    public override string EnglishName => "mcpstart";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
        int port = 0;
        if (mode == RunMode.Scripted)
        {
            var option = new OptionInteger(0, 0, 65535);
            using var input = new GetOption();
            input.SetCommandPrompt("RhinoMCP port (0 = automatic)");
            input.AcceptNothing(true);
            input.AcceptNumber(true, true);
            input.AddOptionInteger("Port", ref option);
            while (true)
            {
                var result = input.Get();
                if (result == GetResult.Option)
                    continue;
                if (result == GetResult.Number)
                {
                    var number = input.Number();
                    if (number < 0 || number > 65535 || number != Math.Floor(number))
                    {
                        RhinoApp.WriteLine($"{number} is not a valid port.");
                        return Result.Failure;
                    }
                    port = (int)number;
                    break;
                }
                if (result == GetResult.Nothing)
                {
                    port = option.CurrentValue;
                    break;
                }
                return Result.Cancel;
            }
        }

        var command = BridgeCommandActions.Start(port);
        RhinoApp.WriteLine(command.Output);
        return command.Success ? Result.Success : Result.Failure;
    }
}

public sealed class MCPStopCommand : Command
{
    public override string EnglishName => "mcpstop";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
        RhinoApp.WriteLine(BridgeCommandActions.Stop());
        return Result.Success;
    }
}

public sealed class MCPVersionCommand : Command
{
    public override string EnglishName => "mcpversion";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
        RhinoApp.WriteLine(BridgeCommandActions.Version());
        return Result.Success;
    }
}
