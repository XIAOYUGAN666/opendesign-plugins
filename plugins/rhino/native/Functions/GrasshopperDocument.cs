using System.Text.Json;
using Grasshopper;
using Grasshopper.GUI.Canvas;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;
using Rhino;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperDocumentCommands
{
    [McpCommand("gh_create_document", ReadOnly = false, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object CreateDocument(RequestContext context, JsonElement parameters)
    {
        var openCanvas = NativeFunctions.Boolean(parameters, "open_canvas", true);
        var makeActive = NativeFunctions.Boolean(parameters, "make_active", true);
        var canvas = context.GrasshopperCanvas;
        if (openCanvas && canvas is null)
        {
            RhinoApp.RunScript(context.RhinoDocument.RuntimeSerialNumber, "_Grasshopper", false);
            RhinoApp.Wait();
            canvas = context.ActiveDocumentSource?.ActiveGrasshopperCanvas;
        }

        var server = Instances.DocumentServer;
        var document = canvas?.Document ?? context.GrasshopperDocument;
        if (document is null && server is { DocumentCount: > 0 })
        {
            document = server.NextAvailableDocument();
            if (document is null && server.DocumentCount == 1)
            {
                document = server[0];
            }
        }

        var created = false;
        if (document is null && NativeFunctions.Boolean(parameters, "new_if_missing", true))
        {
            document = server?.AddNewDocument();
            created = document is not null;
        }

        if (document is not null && makeActive)
        {
            server?.PromoteDocument(document);
            if (canvas is not null)
            {
                canvas.Document = document;
                Instances.InvalidateCanvas();
                Instances.RedrawCanvas();
            }
        }

        return new Dictionary<string, object?>
        {
            ["has_document"] = document is not null,
            ["created"] = created,
            ["made_active"] = document is not null && makeActive,
            ["canvas_open"] = canvas is not null,
            ["file_path"] = document is null ? null : DocumentFileName(document),
            ["object_count"] = document?.ObjectCount ?? 0,
            ["visibility"] = document is null ? null : Visibility(context, document, canvas),
            ["message"] = document is null ? "No active Grasshopper document"
                : created ? "Created Grasshopper document" : "Grasshopper document is available",
        };
    }

    [McpCommand("gh_get_document_info", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object GetDocumentInfo(RequestContext context, JsonElement parameters)
    {
        var document = context.GrasshopperDocument;
        if (document is null)
        {
            return new Dictionary<string, object?>
            {
                ["has_document"] = false,
                ["message"] = "No active Grasshopper document",
            };
        }

        var components = document.Objects.OfType<IGH_Component>().ToArray();
        return new Dictionary<string, object?>
        {
            ["has_document"] = true,
            ["file_path"] = DocumentFileName(document),
            ["is_modified"] = document.IsModified,
            ["object_count"] = document.ObjectCount,
            ["component_count"] = components.Length,
            ["parameter_count"] = document.Objects.OfType<IGH_Param>()
                .Count(parameter => parameter.Attributes?.GetTopLevel.DocObject is not IGH_Component),
            ["group_count"] = document.Objects.OfType<GH_Group>().Count(),
            ["visibility"] = Visibility(context, document, context.GrasshopperCanvas),
            ["components_by_category"] = components.GroupBy(component => component.Category ?? "Unknown")
                .ToDictionary(group => group.Key, group => group.Count()),
        };
    }

    private static string DocumentFileName(GH_Document document) =>
        string.IsNullOrEmpty(document.FilePath) ? "(unsaved)" : Path.GetFileName(document.FilePath);

    private static Dictionary<string, object?> Visibility(
        RequestContext context, GH_Document document, GH_Canvas? canvas
    )
    {
        var previewObjects = document.Objects.OfType<IGH_PreviewObject>().ToArray();
        var enabled = previewObjects.Count(item => !item.Hidden);
        var rhinoCount = context.RhinoDocument.Objects.Count;
        var active = ReferenceEquals(canvas?.Document, document);
        string? warning = null;
        if (canvas is null)
        {
            warning = "Grasshopper has a document, but the editor canvas is not open. Live Grasshopper preview may not be visible in Rhino viewport captures.";
        }
        else if (!active)
        {
            warning = "Grasshopper editor is open, but this document is not the active canvas document.";
        }
        else if (previewObjects.Length > 0 && enabled == 0)
        {
            warning = "All preview-capable Grasshopper objects have preview disabled.";
        }
        else if (enabled > 0 && rhinoCount == 0)
        {
            warning = "Grasshopper preview is live but not baked. Use gh_capture_preview to frame live Grasshopper preview bounds without baking.";
        }
        return new Dictionary<string, object?>
        {
            ["canvas_open"] = canvas is not null,
            ["active_canvas_document"] = active,
            ["preview_capable_object_count"] = previewObjects.Length,
            ["preview_enabled_object_count"] = enabled,
            ["preview_disabled_object_count"] = previewObjects.Length - enabled,
            ["has_preview_enabled_objects"] = enabled > 0,
            ["rhino_document_object_count"] = rhinoCount,
            ["has_baked_rhino_objects"] = rhinoCount > 0,
            ["viewport_warning"] = warning,
        };
    }

    [McpCommand("gh_get_canvas_state", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetCanvasState(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var includeConnections = NativeFunctions.Boolean(parameters, "include_connections", true);
        var includeValues = NativeFunctions.Boolean(parameters, "include_values");
        var maxItems = Math.Max(0, Math.Min(1000, NativeFunctions.Integer(parameters, "max_items", 20)));
        var result = GrasshopperReadback.CanvasObjects(
            document.Objects, includeConnections, includeValues, maxItems);
        result["file_path"] = DocumentFileName(document);
        result["visibility"] = Visibility(context, document, context.GrasshopperCanvas);
        result["object_count"] = document.ObjectCount;
        return result;
    }

    internal static IGH_DocumentObject? FindOwner(GH_Document document, IGH_Param parameter)
    {
        foreach (var item in document.Objects)
        {
            if (ReferenceEquals(item, parameter))
            {
                return item;
            }

            if (GrasshopperHelpers.Inputs(item).Any(input => ReferenceEquals(input, parameter))
                || GrasshopperHelpers.Outputs(item).Any(output => ReferenceEquals(output, parameter)))
            {
                return item;
            }
        }

        return null;
    }
}
