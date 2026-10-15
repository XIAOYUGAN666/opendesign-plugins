using Grasshopper;
using Grasshopper.GUI.Canvas;
using Grasshopper.Kernel;
using Rhino;

namespace RhinoMcpBridge;

internal interface IActiveDocumentSource
{
    ActiveDocumentSnapshot Capture();

    GH_Canvas? ActiveGrasshopperCanvas { get; }
}

internal sealed record ActiveDocumentSnapshot(
    RhinoDoc? RhinoDocument,
    GH_Document? GrasshopperDocument,
    GH_Canvas? GrasshopperCanvas,
    double AbsoluteTolerance,
    double AngleToleranceRadians,
    UnitSystem ModelUnitSystem
);

internal sealed class RhinoActiveDocumentSource : IActiveDocumentSource
{
    private static GH_Canvas? CurrentGrasshopperCanvas => Instances.ActiveCanvas;

    public GH_Canvas? ActiveGrasshopperCanvas => CurrentGrasshopperCanvas;

    public ActiveDocumentSnapshot Capture()
    {
        var document = RhinoDoc.ActiveDoc;
        var canvas = CurrentGrasshopperCanvas;
        var grasshopperDocument = canvas?.Document ?? Instances.ActiveDocument;

        return new ActiveDocumentSnapshot(
            document,
            grasshopperDocument,
            canvas,
            document?.ModelAbsoluteTolerance ?? 0,
            document?.ModelAngleToleranceRadians ?? 0,
            document?.ModelUnitSystem ?? default
        );
    }
}

internal sealed record RequestContext(
    RhinoDoc RhinoDocument,
    GH_Document? GrasshopperDocument,
    GH_Canvas? GrasshopperCanvas,
    double AbsoluteTolerance,
    double AngleToleranceRadians,
    UnitSystem ModelUnitSystem,
    string RequestId,
    CancellationToken CancellationToken
)
{
    internal IActiveDocumentSource? ActiveDocumentSource { get; init; }

    public GH_Document RequireGrasshopperDocument() =>
        GrasshopperDocument
        ?? throw new NativeCommandException(
            "noActiveGrasshopperDocument",
            "No active Grasshopper document is available."
        );
}

internal static class RequestContextFactory
{
    public static RequestContext Capture(
        IActiveDocumentSource source,
        string requestId,
        CancellationToken cancellationToken
    )
    {
        if (source is null)
        {
            throw new ArgumentNullException(nameof(source));
        }

        return CreateContext(source.Capture(), source, requestId, cancellationToken);
    }

    internal static RequestContext CreateContext(
        ActiveDocumentSnapshot snapshot,
        IActiveDocumentSource? source,
        string requestId,
        CancellationToken cancellationToken
    )
    {
        if (snapshot is null)
        {
            throw new ArgumentNullException(nameof(snapshot));
        }

        if (string.IsNullOrWhiteSpace(requestId) || requestId.Length > 128)
        {
            throw new ArgumentException("Request identity is invalid.", nameof(requestId));
        }

        var document = snapshot.RhinoDocument;
        if (document is null)
        {
            throw new NativeCommandException("noActiveDocument", "No active Rhino document is available.");
        }

        return new RequestContext(
            document,
            snapshot.GrasshopperDocument,
            snapshot.GrasshopperCanvas,
            snapshot.AbsoluteTolerance,
            snapshot.AngleToleranceRadians,
            snapshot.ModelUnitSystem,
            requestId,
            cancellationToken
        )
        {
            ActiveDocumentSource = source,
        };
    }

    internal static bool TryOpenGrasshopperDocument(
        RequestContext context,
        GH_Document document,
        bool openCanvas,
        bool makeActive
    )
    {
        if (!openCanvas && !makeActive)
        {
            return false;
        }

        if (openCanvas)
        {
            RhinoApp.RunScript(context.RhinoDocument.RuntimeSerialNumber, "_Grasshopper", false);
        }

        var canvas = context.GrasshopperCanvas ?? context.ActiveDocumentSource?.ActiveGrasshopperCanvas;
        if (canvas is null)
        {
            return false;
        }

        canvas.Document = document;
        return ReferenceEquals(canvas.Document, document);
    }
}

internal sealed class NativeCommandException(string category, string safeMessage)
    : InvalidOperationException(safeMessage)
{
    public string Category { get; } = category;

    public string SafeMessage { get; } = safeMessage;
}
