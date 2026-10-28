using System.Text.Json;
using Grasshopper;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;
using Rhino;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperDocumentCommands
{
    [McpCommand("gh_create_document", ReadOnly = false, SupportsDryRun = false)]
    internal static object CreateDocument(RequestContext context, JsonElement parameters)
    {
        var document = context.GrasshopperDocument;
        var created = false;
        if (document is null)
        {
            if (!NativeFunctions.Boolean(parameters, "new_if_missing", true))
            {
                throw new NativeCommandException(
                    "noActiveGrasshopperDocument",
                    "No active Grasshopper document is available."
                );
            }

            document = Instances.DocumentServer?.AddNewDocument()
                ?? throw new NativeCommandException(
                    "grasshopperUnavailable",
                    "Grasshopper could not create a document."
                );
            created = true;
        }

        var openCanvas = NativeFunctions.Boolean(parameters, "open_canvas", true);
        var makeActive = NativeFunctions.Boolean(parameters, "make_active", true);
        var canvasOpened = RequestContextFactory.TryOpenGrasshopperDocument(
            context,
            document,
            openCanvas,
            makeActive
        );

        return new Dictionary<string, object?>
        {
            ["document"] = DocumentInfo(document),
            ["created"] = created,
            ["made_active"] = makeActive && canvasOpened,
            ["canvas_opened"] = canvasOpened && openCanvas,
        };
    }

    [McpCommand("gh_get_document_info", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object GetDocumentInfo(RequestContext context, JsonElement parameters) =>
        DocumentInfo(GrasshopperHelpers.RequireDocument(context));

    [McpCommand("gh_get_canvas_state", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetCanvasState(RequestContext context, JsonElement parameters)
    {
        var document = GrasshopperHelpers.RequireDocument(context);
        var includeConnections = NativeFunctions.Boolean(parameters, "include_connections", true);
        var includeValues = NativeFunctions.Boolean(parameters, "include_values");
        var maxItems = Math.Max(0, Math.Min(1000, NativeFunctions.Integer(parameters, "max_items", 20)));
        var objects = document.Objects
            .Where(item => item is not null)
            .Select(item => GrasshopperHelpers.ObjectInfo(item, includeValues, maxItems))
            .ToArray();

        return new Dictionary<string, object?>
        {
            ["document"] = DocumentInfo(document),
            ["objects"] = objects,
            ["connections"] = includeConnections ? Connections(document) : Array.Empty<object>(),
            ["canvas_open"] = context.GrasshopperCanvas is { IsDocument: true },
            ["object_count"] = objects.Length,
        };
    }

    internal static Dictionary<string, object?> DocumentInfo(GH_Document document) => new()
    {
        ["document_id"] = document.DocumentID.ToString("D"),
        ["object_count"] = document.ObjectCount,
        ["component_count"] = document.Objects.OfType<GH_Component>().Count(),
        ["parameter_count"] = document.Objects.OfType<IGH_Param>().Count(),
        ["group_count"] = document.Objects.OfType<GH_Group>().Count(),
        ["is_modified"] = document.IsModified,
        ["solutions_enabled"] = GH_Document.EnableSolutions,
    };

    internal static object[] Connections(GH_Document document)
    {
        var result = new List<object>();
        foreach (var sourceOwner in document.Objects)
        {
            var outputs = GrasshopperHelpers.Outputs(sourceOwner);
            for (var outputIndex = 0; outputIndex < outputs.Length; outputIndex++)
            {
                var output = outputs[outputIndex];
                foreach (var recipient in output.Recipients)
                {
                    var targetOwner = FindOwner(document, recipient);
                    if (targetOwner is null)
                    {
                        continue;
                    }

                    var inputs = GrasshopperHelpers.Inputs(targetOwner);
                    var inputIndex = Array.FindIndex(inputs, input => ReferenceEquals(input, recipient));
                    result.Add(new Dictionary<string, object?>
                    {
                        ["source_instance_id"] = sourceOwner.InstanceGuid.ToString("D"),
                        ["source_output_index"] = outputIndex,
                        ["source_output_name"] = output.NickName,
                        ["target_instance_id"] = targetOwner.InstanceGuid.ToString("D"),
                        ["target_input_index"] = inputIndex,
                        ["target_input_name"] = recipient.NickName,
                    });
                }
            }
        }

        return result.ToArray();
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
