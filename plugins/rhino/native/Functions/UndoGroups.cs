using System.Text.Json;
using System.Threading;
using Rhino;

namespace RhinoMcpBridge.Functions;

// Adapted from RhinoMCP's UndoGroups.cs. All state changes run on the Rhino UI thread.
internal sealed class UndoGroups
{
    private volatile Group? _open;
    private Group? _lastUnrequested;

    internal bool HasOpenGroup => _open is not null;

    private sealed class Group(RhinoDoc document, uint record, string owner, string name, TimeSpan idle)
    {
        internal RhinoDoc Document { get; } = document;
        internal uint Record { get; } = record;
        internal string Owner { get; } = owner;
        internal string Name { get; } = name;
        internal TimeSpan Idle { get; } = idle;
        internal DateTime Opened { get; } = DateTime.UtcNow;
        internal DateTime LastCommand { get; set; } = DateTime.UtcNow;
        internal DateTime Closed { get; set; }
        internal string EndReason { get; set; } = "";
        internal int CommandCount { get; set; }
        internal Timer? Timer { get; set; }
        internal bool OwnerNotified { get; set; }
        internal bool OwnerInCommand { get; set; }
    }

    [McpCommand("begin_undo_group", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object Begin(RequestContext context, JsonElement parameters) =>
        RequireService(context).BeginCore(context, parameters);

    [McpCommand("end_undo_group", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object End(RequestContext context, JsonElement parameters) =>
        RequireService(context).EndCore(context, NativeFunctions.Boolean(parameters, "force"));

    private static UndoGroups RequireService(RequestContext context) => context.UndoGroupService
        ?? throw new NativeCommandException("rhino_error", "Undo grouping is unavailable outside the Bridge.");

    private object BeginCore(RequestContext context, JsonElement parameters)
    {
        var raw = NativeFunctions.Text(parameters, "name") ?? "";
        var name = raw.Split(new[] { '\r', '\n' }, 2)[0].Trim();
        if (name.Length == 0)
            throw new NativeCommandException("invalid_argument", "Name the undo group.");
        if (name.Length > 80)
            name = name.Substring(0, 80);
        var timeout = NativeFunctions.Integer(parameters, "idle_timeout_seconds", 120);
        if (timeout < 1 || timeout > 3600)
            throw new NativeCommandException("invalid_argument", "idle_timeout_seconds must be between 1 and 3600.");
        if (Live(context.RhinoDocument) is Group open)
            throw new NativeCommandException("invalid_argument", $"Undo group '{open.Name}' is already open. Groups do not nest.");

        var document = context.RhinoDocument;
        var label = "MCP: " + name;
        var record = document.BeginUndoRecord(label);
        if (record == 0)
            throw new NativeCommandException("rhino_error", document.UndoRecordingEnabled
                ? "Rhino is already recording an undo step. Finish that command and retry."
                : "Undo is turned off in this document.");

        var group = new Group(document, record, context.CommandOwner, name, TimeSpan.FromSeconds(timeout));
        _open = group;
        _lastUnrequested = null;
        RhinoDoc.CloseDocument += OnCloseDocument;
        RhinoDoc.ActiveDocumentChanged += OnActiveDocumentChanged;
        group.Timer = new Timer(_ => RhinoApp.InvokeOnUiThread(new Action(() => CheckIdle(group))),
            null, group.Idle, Timeout.InfiniteTimeSpan);

        return new Dictionary<string, object?>
        {
            ["name"] = name,
            ["undo_description"] = label,
            ["document_name"] = string.IsNullOrEmpty(document.Name) ? null : document.Name,
            ["idle_timeout_seconds"] = timeout,
            ["message"] = $"Undo group '{name}' is open: changes until end_undo_group are one undo step, '{label}'.",
        };
    }

    private object EndCore(RequestContext context, bool force)
    {
        var group = Live(context.RhinoDocument);
        if (group is null)
        {
            var result = new Dictionary<string, object?> { ["ended"] = false, ["message"] = "No undo group is open." };
            if (_lastUnrequested is Group previous)
            {
                if (previous.Owner == context.CommandOwner)
                    previous.OwnerNotified = true;
                result["last_group"] = Summary(previous);
                result["message"] = $"No undo group is open. '{previous.Name}' already ended ({previous.EndReason}).";
            }
            return result;
        }

        var owns = group.Owner == context.CommandOwner;
        if (!owns && !force)
            throw new NativeCommandException("invalid_argument",
                $"Undo group '{group.Name}' was begun by another client. Wait for it to end or request force=true.");

        Close(group, owns ? "requested" : "forced");
        var summary = Summary(group);
        summary["ended"] = true;
        summary["message"] = $"Undo group '{group.Name}' ended: one Undo reverts its changes.";
        return summary;
    }

    internal bool PrepareCommand(RequestContext context, string name, bool readOnly)
    {
        var group = Live(context.RhinoDocument);
        if (group is null)
            return false;
        var owns = group.Owner == context.CommandOwner;
        if (owns)
            group.LastCommand = DateTime.UtcNow;
        if (name is "begin_undo_group" or "end_undo_group")
            return false;
        if (name is "undo" or "redo")
            throw new NativeCommandException("invalid_argument", $"End undo group '{group.Name}' before using {name}.");
        if (!readOnly && !owns)
            throw new NativeCommandException("invalid_argument",
                $"Undo group '{group.Name}' belongs to another client. End it before editing.");
        if (owns)
        {
            group.OwnerInCommand = true;
            if (!readOnly)
                group.CommandCount++;
        }
        return owns && !readOnly;
    }

    internal void FinishCommand(RequestContext context, bool mutating, object? result)
    {
        if (Live(context.RhinoDocument) is Group group && group.Owner == context.CommandOwner)
        {
            group.OwnerInCommand = false;
            group.LastCommand = DateTime.UtcNow;
        }
        if (mutating && _lastUnrequested is Group previous && previous.Owner == context.CommandOwner
            && !previous.OwnerNotified && result is IDictionary<string, object?> body)
        {
            previous.OwnerNotified = true;
            var notice = Summary(previous);
            notice["message"] = $"Undo group '{previous.Name}' ended ({previous.EndReason}). Begin a new group to group later edits.";
            body["_undo_group"] = notice;
        }
    }

    internal void ClientDisconnected(string owner)
    {
        if (_open is Group group && group.Owner == owner)
            TryCloseAutomatically(group, "client_disconnected");
    }

    private Group? Live(RhinoDoc document)
    {
        if (_open is not Group group)
            return null;
        if (group.Document.RuntimeSerialNumber != document.RuntimeSerialNumber)
            Close(group, "document_changed");
        else if (group.Document.CurrentUndoRecordSerialNumber != group.Record)
            Close(group, "record_ended_elsewhere");
        return _open;
    }

    private void CheckIdle(Group group)
    {
        if (!ReferenceEquals(_open, group))
            return;
        var remaining = group.OwnerInCommand ? group.Idle : group.Idle - (DateTime.UtcNow - group.LastCommand);
        if (remaining <= TimeSpan.Zero)
            TryCloseAutomatically(group, "idle_timeout");
        else
            group.Timer?.Change(remaining, Timeout.InfiniteTimeSpan);
    }

    private void OnCloseDocument(object? sender, DocumentEventArgs args)
    {
        if (_open is Group group && args.DocumentSerialNumber == group.Document.RuntimeSerialNumber)
            TryCloseAutomatically(group, "document_closed");
    }

    private void OnActiveDocumentChanged(object? sender, DocumentEventArgs args)
    {
        if (_open is Group group && args.DocumentSerialNumber != group.Document.RuntimeSerialNumber)
            TryCloseAutomatically(group, "document_changed");
    }

    internal void Shutdown()
    {
        if (_open is Group group)
            TryCloseAutomatically(group, "client_disconnected");
    }

    private void TryCloseAutomatically(Group group, string reason)
    {
        try
        {
            Close(group, reason);
        }
        catch (NativeCommandException)
        {
            // Keep record ownership until Rhino confirms closure. A later command,
            // document event or timer tick can settle the record.
            group.Timer?.Change(TimeSpan.FromSeconds(1), Timeout.InfiniteTimeSpan);
        }
    }

    private void Close(Group group, string reason)
    {
        var doc = RhinoDoc.FromRuntimeSerialNumber(group.Document.RuntimeSerialNumber);
        if (doc?.CurrentUndoRecordSerialNumber == group.Record)
        {
            try
            {
                doc.EndUndoRecord(group.Record);
            }
            catch (Exception)
            {
                throw new NativeCommandException("rhino_error", "Rhino could not close the undo group record.");
            }
            if (doc.CurrentUndoRecordSerialNumber == group.Record)
                throw new NativeCommandException("rhino_error", "Rhino could not close the undo group record.");
        }
        _open = null;
        group.Timer?.Dispose();
        RhinoDoc.CloseDocument -= OnCloseDocument;
        RhinoDoc.ActiveDocumentChanged -= OnActiveDocumentChanged;

        group.Closed = DateTime.UtcNow;
        group.EndReason = reason;
        if (reason != "requested")
            _lastUnrequested = group;
    }

    private static Dictionary<string, object?> Summary(Group group) => new()
    {
        ["name"] = group.Name,
        ["end_reason"] = group.EndReason,
        ["command_count"] = group.CommandCount,
        ["duration_seconds"] = Math.Round(Math.Max(0, (group.Closed - group.Opened).TotalSeconds), 1),
    };
}
