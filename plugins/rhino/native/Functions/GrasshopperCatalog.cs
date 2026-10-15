using System.Text.Json;
using Grasshopper.Kernel;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperCatalog
{
    [McpCommand("gh_search_components", ReadOnly = true, SupportsDryRun = false)]
    internal static object SearchComponents(RequestContext context, JsonElement parameters)
    {
        var query = NativeFunctions.Text(parameters, "query");
        var category = NativeFunctions.Text(parameters, "category");
        var limit = NativeFunctions.Integer(parameters, "limit", 50);
        var matches = GrasshopperHelpers.SearchProxies(query, category, limit);
        return new Dictionary<string, object?>
        {
            ["query"] = query,
            ["category"] = category,
            ["results"] = matches.Select(proxy => Summary(proxy, includeDescription: true)).ToArray(),
            ["count"] = matches.Count,
        };
    }

    [McpCommand("gh_batch_search_components", ReadOnly = true, SupportsDryRun = false)]
    internal static object BatchSearchComponents(RequestContext context, JsonElement parameters)
    {
        var queries = StringArray(parameters, "queries");
        var maxMatches = Math.Max(1, Math.Min(50, NativeFunctions.Integer(parameters, "max_matches", 5)));
        var searches = queries.Select(query =>
        {
            var matches = GrasshopperHelpers.SearchProxies(query, null, maxMatches)
                .Select(proxy => Summary(proxy, includeDescription: true))
                .ToArray();
            return new Dictionary<string, object?>
            {
                ["query"] = query,
                ["result"] = matches.FirstOrDefault(),
                ["matches"] = matches,
                ["ambiguous"] = matches.Length > 1,
            };
        }).ToArray();
        return new Dictionary<string, object?>
        {
            ["results"] = searches.Select(item => item["result"]).ToArray(),
            ["matches"] = searches,
            ["ambiguous"] = searches.Where(item => (bool)item["ambiguous"]!).ToArray(),
            ["count"] = searches.Length,
        };
    }

    [McpCommand("gh_list_component_categories", ReadOnly = true, SupportsDryRun = false)]
    internal static object ListCategories(RequestContext context, JsonElement parameters)
    {
        var categories = GrasshopperHelpers.Proxies
            .GroupBy(proxy => proxy.Desc.Category ?? string.Empty, StringComparer.OrdinalIgnoreCase)
            .OrderBy(group => group.Key, StringComparer.OrdinalIgnoreCase)
            .Select(group => new Dictionary<string, object?>
            {
                ["name"] = group.Key,
                ["subcategories"] = group
                    .Select(proxy => proxy.Desc.SubCategory ?? string.Empty)
                    .Where(name => name.Length > 0)
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .OrderBy(name => name, StringComparer.OrdinalIgnoreCase)
                    .ToArray(),
                ["component_count"] = group.Count(),
            })
            .ToArray();
        return new Dictionary<string, object?>
        {
            ["categories"] = categories,
            ["count"] = categories.Length,
        };
    }

    [McpCommand("gh_get_available_components", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetAvailableComponents(RequestContext context, JsonElement parameters)
    {
        var category = NativeFunctions.Text(parameters, "category");
        var includeDescription = NativeFunctions.Boolean(parameters, "include_description");
        var limit = Math.Max(1, Math.Min(500, NativeFunctions.Integer(parameters, "limit", 500)));
        var all = GrasshopperHelpers.SearchProxies(null, category, 5000);
        var selected = all.Take(limit).ToArray();
        return new Dictionary<string, object?>
        {
            ["components"] = selected.Select(proxy => Summary(proxy, includeDescription)).ToArray(),
            ["count"] = selected.Length,
            ["total_count"] = all.Count,
            ["truncated"] = all.Count > selected.Length,
        };
    }

    [McpCommand("gh_get_component_type_info", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetComponentTypeInfo(RequestContext context, JsonElement parameters)
    {
        var proxy = ResolveProxy(parameters);
        return GrasshopperHelpers.ComponentTypeInfo(proxy);
    }

    [McpCommand("gh_batch_get_component_type_info", ReadOnly = true, SupportsDryRun = false)]
    internal static object BatchGetComponentTypeInfo(RequestContext context, JsonElement parameters)
    {
        var components = NativeFunctions.Property(parameters, "components");
        if (components.ValueKind != JsonValueKind.Array)
        {
            throw new NativeCommandException("invalidParameters", "Component selectors must be an array.");
        }

        var results = components.EnumerateArray().Select(selector =>
        {
            try
            {
                return new Dictionary<string, object?>
                {
                    ["selector"] = SummarySelector(selector),
                    ["component"] = GrasshopperHelpers.ComponentTypeInfo(ResolveProxy(selector)),
                    ["error"] = null,
                };
            }
            catch (NativeCommandException exception)
            {
                return new Dictionary<string, object?>
                {
                    ["selector"] = SummarySelector(selector),
                    ["component"] = null,
                    ["error"] = new Dictionary<string, object?>
                    {
                        ["category"] = exception.Category,
                        ["message"] = exception.SafeMessage,
                    },
                };
            }
        }).ToArray();
        return new Dictionary<string, object?>
        {
            ["components"] = results,
            ["count"] = results.Length,
            ["found_count"] = results.Count(item => item["component"] is not null),
        };
    }

    private static IGH_ObjectProxy ResolveProxy(JsonElement parameters)
    {
        var guid = NativeFunctions.Text(parameters, "guid")
            ?? NativeFunctions.Text(parameters, "component_guid");
        var name = NativeFunctions.Text(parameters, "name")
            ?? NativeFunctions.Text(parameters, "component_name");
        return GrasshopperHelpers.RequireProxy(name, guid);
    }

    private static object Summary(IGH_ObjectProxy proxy, bool includeDescription) =>
        new Dictionary<string, object?>
        {
            ["guid"] = proxy.Guid.ToString("D"),
            ["name"] = proxy.Desc.Name,
            ["nickname"] = proxy.Desc.NickName,
            ["category"] = proxy.Desc.Category,
            ["subcategory"] = proxy.Desc.SubCategory,
            ["description"] = includeDescription ? proxy.Desc.Description : null,
            ["obsolete"] = proxy.Obsolete,
        };

    private static string[] StringArray(JsonElement parameters, string key)
    {
        var values = NativeFunctions.Property(parameters, key);
        if (values.ValueKind != JsonValueKind.Array)
        {
            throw new NativeCommandException("invalidParameters", "Search queries must be an array.");
        }

        return values.EnumerateArray()
            .Select(value => value.ValueKind == JsonValueKind.String ? value.GetString() : null)
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Select(value => value!.Trim())
            .ToArray();
    }

    private static object SummarySelector(JsonElement selector) => new Dictionary<string, object?>
    {
        ["name"] = NativeFunctions.Text(selector, "name") ?? NativeFunctions.Text(selector, "component_name"),
        ["guid"] = NativeFunctions.Text(selector, "guid") ?? NativeFunctions.Text(selector, "component_guid"),
    };
}
