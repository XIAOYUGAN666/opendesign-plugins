using System.Text;
using System.Text.Json;
using Rhino.Runtime;

namespace RhinoMcpBridge.Functions;

internal static class ExecuteRhinoscript
{
    [McpCommand("execute_rhinoscript_python_code", ReadOnly = false, SupportsDryRun = false)]
    internal static object Execute(RequestContext context, JsonElement parameters)
    {
        var code = NativeFunctions.Text(parameters, "code");
        if (code is null || code.Length == 0)
        {
            throw new NativeCommandException("invalidParameters", "Python code is required.");
        }

        var output = new StringBuilder();
        try
        {
            return NativeFunctions.Mutate(context, "Execute MCP RhinoScript Python code", () =>
            {
                var script = PythonScript.Create();
                if (script is null)
                {
                    throw new NativeCommandException(
                        "scriptRuntimeUnavailable",
                        "Rhino's Python scripting runtime is unavailable."
                    );
                }

                script.Output = text => output.Append(text);
                script.SetupScriptContext(context.RhinoDocument);
                if (!script.ExecuteScript(code))
                {
                    throw new NativeCommandException(
                        "scriptExecutionFailed",
                        "Python execution failed; Rhino changes were rolled back."
                    );
                }

                return Result(true, output, "Python code executed successfully.");
            });
        }
        catch (OperationCanceledException)
        {
            return Result(false, output, "Python execution was cancelled; Rhino changes were rolled back.");
        }
        catch (NativeCommandException exception)
        {
            return Result(false, output, exception.SafeMessage);
        }
        catch
        {
            return Result(false, output, "Python execution failed; Rhino changes were rolled back.");
        }
    }

    private static Dictionary<string, object?> Result(bool success, StringBuilder output, string message) =>
        new()
        {
            ["success"] = success,
            ["output"] = output.ToString(),
            ["message"] = message,
        };
}
