using System.Text.Json;
using Grasshopper;
using Grasshopper.Kernel;
using Newtonsoft.Json.Linq;
using Rhino;

namespace RhinoMcpBridge.Functions;

internal sealed partial class NativeSelfTests(RhinoDoc document, Action<string> write)
{
    private readonly RhinoDoc _document = document;
    private readonly Action<string> _write = write;
    private readonly RhinoActiveDocumentSource _source = new();
    private readonly UndoGroups _undoGroups = new();
    private readonly CommandRegistry _registry = new();

    private RequestContext Context() => RequestContextFactory.CreateContext(
        _source.Capture() with
        {
            RhinoDocument = _document,
            AbsoluteTolerance = _document.ModelAbsoluteTolerance,
            AngleToleranceRadians = _document.ModelAngleToleranceRadians,
            ModelUnitSystem = _document.ModelUnitSystem,
        },
        _source, Guid.NewGuid().ToString("N"), CancellationToken.None
    ) with { CommandOwner = "native-self-test", UndoGroupService = _undoGroups };

    private JObject Invoke(string name, JObject parameters) => (JObject)InvokeValue(name, parameters);

    private JToken InvokeValue(string name, JObject parameters)
    {
        using var arguments = JsonDocument.Parse(parameters.ToString(Newtonsoft.Json.Formatting.None));
        var context = Context();
        var command = _registry.Get(name);
        context = context with { InUndoGroup = _undoGroups.PrepareCommand(context, name, command.ReadOnly) };
        object? result = null;
        try
        {
            result = _registry.Invoke(name, context, arguments.RootElement);
            return JToken.Parse(JsonSerializer.Serialize(result));
        }
        finally
        {
            _undoGroups.FinishCommand(context, !command.ReadOnly
                && name is not ("begin_undo_group" or "end_undo_group"), result);
        }
    }

    private GH_Document GetActiveGrasshopperDocument() => Context().RequireGrasshopperDocument();

    private void RedrawGrasshopperCanvas(System.Drawing.PointF focus)
    {
        var canvas = _source.ActiveGrasshopperCanvas;
        if (canvas is null)
            return;
        canvas.Viewport.Focus(focus);
        Instances.InvalidateCanvas();
        Instances.RedrawCanvas();
    }

    private static HashSet<Guid> SnapshotObjectIds(RhinoDoc doc) =>
        doc.Objects.GetObjectList(Rhino.DocObjects.ObjectType.AnyObject)
            .Where(item => !item.IsDeleted && !item.IsReference).Select(item => item.Id).ToHashSet();

    private static JObject BuildDelta(IEnumerable<Guid> before, IEnumerable<Guid> after) =>
        JObject.FromObject(BridgePerception.BuildDelta(before, after));

    private static JObject BuildHealth(RhinoDoc doc, HashSet<Guid> before, HashSet<Guid> after) =>
        JObject.FromObject(BridgePerception.BuildHealth(BridgePerception.CaptureHealth(doc, before, after)));
}
