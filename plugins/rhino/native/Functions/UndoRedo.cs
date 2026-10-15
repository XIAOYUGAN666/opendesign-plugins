using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class UndoRedo
{
    [McpCommand("undo", ReadOnly = false, SupportsDryRun = false)]
    internal static object Undo(RequestContext context, JsonElement parameters) =>
        Apply(context, Math.Max(1, Math.Min(100, NativeFunctions.Integer(parameters, "steps", 1))), undo: true);

    [McpCommand("redo", ReadOnly = false, SupportsDryRun = false)]
    internal static object Redo(RequestContext context, JsonElement parameters) =>
        Apply(context, Math.Max(1, Math.Min(100, NativeFunctions.Integer(parameters, "steps", 1))), undo: false);

    private static object Apply(RequestContext context, int steps, bool undo)
    {
        var completed = 0;
        for (; completed < steps; completed++)
        {
            context.CancellationToken.ThrowIfCancellationRequested();
            var succeeded = undo ? context.RhinoDocument.Undo() : context.RhinoDocument.Redo();
            if (!succeeded)
            {
                break;
            }
        }

        context.RhinoDocument.Views.Redraw();
        return new Dictionary<string, object?>
        {
            ["success"] = completed > 0,
            ["completed"] = completed,
            ["message"] = $"{(undo ? "Undid" : "Redid")} {completed} of {steps} requested step(s).",
        };
    }
}
