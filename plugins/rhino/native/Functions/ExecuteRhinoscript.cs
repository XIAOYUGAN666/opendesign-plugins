using System.Text;
using System.Text.Json;
using Rhino;
using Rhino.Runtime;

namespace RhinoMcpBridge.Functions;

internal static class ExecuteRhinoscript
{
    [McpCommand("execute_rhinoscript_python_code", ReadOnly = false, SupportsDryRun = false)]
    internal static object Execute(RequestContext context, JsonElement parameters)
    {
        var code = NativeFunctions.Text(parameters, "code");
        if (string.IsNullOrEmpty(code))
            throw new NativeCommandException("invalid_argument", "Python code is required.");

        return NativeFunctions.Mutate(context, "Execute MCP RhinoScript Python code", () =>
        {
            var script = PythonScript.Create()
                ?? throw new NativeCommandException("scriptRuntimeUnavailable", "Rhino's Python scripting runtime is unavailable.");
            var printed = new StringBuilder();
            script.Output = text => printed.Append(text);
            script.SetupScriptContext(context.RhinoDocument);
            var previousCapture = RhinoApp.CommandWindowCaptureEnabled;
            RhinoApp.CommandWindowCaptureEnabled = true;
            string[] commandLines;
            bool success;
            string? message = null;
            try
            {
                try
                {
                    success = script.ExecuteScript(code);
                    if (!success)
                        message = "Script execution returned false (no exception raised).";
                }
                catch (Exception)
                {
                    success = false;
                    message = "Python execution failed.";
                }
                commandLines = RhinoApp.CapturedCommandWindowStrings(true) ?? Array.Empty<string>();
            }
            finally
            {
                RhinoApp.CommandWindowCaptureEnabled = previousCapture;
            }

            var commandOutput = WithoutEchoedPrints(commandLines, printed.ToString());
            if (commandOutput.Length > 0)
            {
                if (printed.Length > 0 && printed[printed.Length - 1] != '\n')
                    printed.Append('\n');
                printed.Append(string.Join("\n", commandOutput));
            }

            var result = new Dictionary<string, object?> { ["success"] = success, ["output"] = printed.ToString() };
            if (message is not null)
                result["message"] = message;
            return result;
        });
    }

    internal static string[] WithoutEchoedPrints(string[] commandLines, string printOutput)
    {
        var printed = printOutput.Split('\n').Select(line => line.TrimEnd('\r'))
            .Where(line => line.Length > 0).ToArray();
        var remaining = new List<string>(commandLines.Length);
        var next = 0;
        foreach (var line in commandLines)
        {
            if (next < printed.Length && line.TrimEnd('\r', '\n') == printed[next])
            {
                next++;
                continue;
            }

            remaining.Add(line);
        }
        return remaining.ToArray();
    }
}
