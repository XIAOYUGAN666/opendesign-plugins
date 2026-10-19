using System.Text.Json;

namespace RhinoMcpBridge.Functions;

internal static class UndoRedo
{
    [McpCommand("undo", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Undo(RequestContext context, JsonElement parameters) =>
        Apply(context, Math.Max(1, Math.Min(100, NativeFunctions.Integer(parameters, "steps", 1))), undo: true);

    [McpCommand("redo", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Redo(RequestContext context, JsonElement parameters) =>
        Apply(context, Math.Max(1, Math.Min(100, NativeFunctions.Integer(parameters, "steps", 1))), undo: false);

    private static object Apply(RequestContext context, int steps, bool undo)
    {
        var completed = 0;
        for (; completed < steps; completed++)
        {
            context.CancellationToken.ThrowIfCancellationRequested();
            var succeeded = ApplyStep(context.RhinoDocument, undo);
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

    internal static bool ApplyStep(Rhino.RhinoDoc doc, bool undo) => UndoRedoStep.Execute(
        undo, undo ? doc.Undo : doc.Redo,
        () => doc.CurrentUndoRecordSerialNumber, () => doc.NextUndoRecordSerialNumber,
        () => doc.UndoActive, () => doc.RedoActive, serial => doc.EndUndoRecord(serial)
    );
}

internal static class UndoRedoStep
{
    internal static bool Execute(
        bool undo, Func<bool> apply, Func<uint> current, Func<uint> next,
        Func<bool> undoActive, Func<bool> redoActive, Func<uint, bool> close
    )
    {
        if (current() != 0 || undoActive() || redoActive())
        {
            throw new NativeCommandException("undoUnavailable", "A Rhino undo record is already active; no undo or redo was started.");
        }

        // RhinoDoc.Undo/Redo starts an inverse record. Outside a Rhino command,
        // the caller must finish that exact record before another step can run.
        var inverse = next();
        if (inverse == 0 || inverse == uint.MaxValue)
        {
            throw new NativeCommandException("undoUnavailable", "Rhino could not reserve an identifiable undo or redo record.");
        }
        var applied = apply();
        if (!applied && current() == 0 && next() == inverse && !undoActive() && !redoActive())
        {
            return false;
        }

        if (!applied || current() != inverse || next() != inverse + 1
            || undoActive() != undo || redoActive() == undo)
        {
            throw new NativeCommandException("undoRecordFailed", "The Rhino undo or redo result could not be verified; no other record was closed.");
        }

        bool closed;
        try { closed = close(inverse); }
        catch { closed = false; }
        if (!closed || current() != 0 || next() != inverse + 1 || undoActive() || redoActive())
        {
            throw new NativeCommandException("undoRecordFailed", "Rhino could not confirm closure of the undo or redo record.");
        }
        return true;
    }
}
