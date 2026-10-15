using System.Reflection;
using System.Text.Json;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class GrasshopperCommandTests
{
    private static readonly string[] ExpectedCommands =
    {
        "gh_add_component",
        "gh_batch_get_component_type_info",
        "gh_batch_search_components",
        "gh_build_graph",
        "gh_capture_preview",
        "gh_clear_canvas",
        "gh_clear_graph",
        "gh_connect_components",
        "gh_create_document",
        "gh_delete_component",
        "gh_disconnect_components",
        "gh_expire_solution",
        "gh_get_available_components",
        "gh_get_canvas_state",
        "gh_get_component_info",
        "gh_get_component_type_info",
        "gh_get_document_info",
        "gh_get_graph",
        "gh_get_parameter_value",
        "gh_layout_components",
        "gh_list_component_categories",
        "gh_list_components",
        "gh_mutate_graph",
        "gh_run_solution",
        "gh_search_components",
        "gh_set_parameter_value",
        "gh_update_component",
    };

    [Fact]
    public void Grasshopper_dispatch_handlers_cover_the_provider_command_set()
    {
        using var native = NativeTestAssembly.Load();
        var attributeType = native.Assembly.GetType(
            "RhinoMcpBridge.McpCommandAttribute",
            throwOnError: true
        )!;
        var actual = native
            .Assembly
            .GetTypes()
            .SelectMany(type =>
                type.GetMethods(
                    BindingFlags.Public
                        | BindingFlags.NonPublic
                        | BindingFlags.Static
                        | BindingFlags.Instance
                )
            )
            .Select(method => method.GetCustomAttributes(attributeType, inherit: false).SingleOrDefault())
            .Where(attribute => attribute is not null)
            .Select(attribute =>
                attributeType
                    .GetProperty(
                        "CommandName",
                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance
                    )!
                    .GetValue(attribute) as string
            )
            .Where(name => name is not null && name.StartsWith("gh_", StringComparison.Ordinal))
            .Order(StringComparer.Ordinal)
            .ToArray();

        Assert.Equal(ExpectedCommands.Order(StringComparer.Ordinal), actual);
    }

    [Fact]
    public void Document_inspection_reports_no_active_grasshopper_document()
    {
        using var native = NativeTestAssembly.Load();
        var contextType = native.Assembly.GetType("RhinoMcpBridge.RequestContext", throwOnError: true)!;
        var constructor = contextType.GetConstructors(
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
        ).Single(candidate => candidate.GetParameters().Length == 8);
        var unitSystemType = constructor.GetParameters()[5].ParameterType;
        var context = constructor.Invoke(
            new object?[]
            {
                null,
                null,
                null,
                0.01,
                0.017453292519943295,
                Enum.ToObject(unitSystemType, 0),
                "request-no-grasshopper-document",
                CancellationToken.None,
            }
        );
        var registryType = native.Assembly.GetType("RhinoMcpBridge.CommandRegistry", throwOnError: true)!;
        var registry = Activator.CreateInstance(registryType, new object?[] { null });
        var invoke = registryType.GetMethod("Invoke", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;
        using var parameters = JsonDocument.Parse("{}");

        var wrapped = Assert.Throws<TargetInvocationException>(
            () => invoke.Invoke(registry, new[] { "gh_get_document_info", context, parameters.RootElement })
        );
        var category = wrapped.InnerException?.GetType()
            .GetProperty("Category", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            ?.GetValue(wrapped.InnerException) as string;

        Assert.Equal("noActiveGrasshopperDocument", category);
    }

    [Fact]
    public void Grasshopper_branch_items_accept_the_sdk_non_generic_object_shape()
    {
        using var native = NativeTestAssembly.Load();
        var helperType = native.Assembly.GetType(
            "RhinoMcpBridge.Functions.GrasshopperHelpers",
            throwOnError: true
        )!;
        var serializeBranchItem = helperType.GetMethod(
            "SerializeBranchItem",
            BindingFlags.NonPublic | BindingFlags.Static
        );

        Assert.NotNull(serializeBranchItem);
        Assert.Equal("branch text", serializeBranchItem!.Invoke(null, new object?[] { "branch text" }));
        Assert.Null(serializeBranchItem.Invoke(null, new object?[] { null }));
    }
}
