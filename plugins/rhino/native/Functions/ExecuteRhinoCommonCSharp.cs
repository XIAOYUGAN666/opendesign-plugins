using System.Text;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp.Scripting;
using Microsoft.CodeAnalysis.Scripting;
using Rhino;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

public sealed class RhinoCSharpScriptGlobals(RhinoDoc document, StringBuilder output)
{
    public RhinoDoc doc { get; } = document;

    public StringBuilder output { get; } = output;
}

internal static class ExecuteRhinoCommonCSharp
{
    private static readonly ScriptOptions Options = ScriptOptions
        .Default
        .WithImports(
            "System",
            "System.Collections.Generic",
            "System.Linq",
            "System.Text",
            "Rhino",
            "Rhino.Geometry",
            "Rhino.DocObjects",
            "Rhino.Commands"
        )
        .AddReferences(
            typeof(object).Assembly,
            typeof(Enumerable).Assembly,
            typeof(StringBuilder).Assembly,
            typeof(RhinoDoc).Assembly,
            typeof(Point3d).Assembly
        );

    [McpCommand("execute_rhinocommon_csharp_code", ReadOnly = false, SupportsDryRun = false)]
    internal static object Execute(RequestContext context, JsonElement parameters)
    {
        var code = NativeFunctions.Text(parameters, "code");
        if (code is null || code.Length == 0)
        {
            throw new NativeCommandException("invalidParameters", "C# code is required.");
        }

        var output = new StringBuilder();
        try
        {
            return NativeFunctions.Mutate(context, "Execute MCP RhinoCommon C# code", () =>
            {
                CSharpScript
                    .RunAsync(
                        code,
                        Options,
                        new RhinoCSharpScriptGlobals(context.RhinoDocument, output),
                        cancellationToken: context.CancellationToken
                    )
                    .GetAwaiter()
                    .GetResult();

                return Result(success: true, output, "C# code executed successfully.");
            });
        }
        catch (CompilationErrorException exception)
        {
            return Result(false, output, CompilationMessage(exception));
        }
        catch (OperationCanceledException)
        {
            return Result(false, output, "C# execution was cancelled; Rhino changes were rolled back.");
        }
        catch
        {
            return Result(false, output, "C# execution failed; Rhino changes were rolled back.");
        }
    }

    private static string CompilationMessage(CompilationErrorException exception)
    {
        var errors = exception
            .Diagnostics.Where(diagnostic => diagnostic.Severity == DiagnosticSeverity.Error)
            .Select(diagnostic => $"{diagnostic.Id}: {diagnostic.GetMessage()}")
            .Take(8)
            .ToArray();
        return errors.Length == 0
            ? "C# compilation failed; Rhino changes were rolled back."
            : "C# compilation failed; Rhino changes were rolled back. " + string.Join(" ", errors);
    }

    private static Dictionary<string, object?> Result(bool success, StringBuilder output, string message) =>
        new()
        {
            ["success"] = success,
            ["output"] = output.ToString(),
            ["message"] = message,
        };
}
