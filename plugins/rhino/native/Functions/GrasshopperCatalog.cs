using System.Text.Json;
using Grasshopper.Kernel;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperCatalog
{
    [McpCommand("gh_search_components", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object SearchComponents(RequestContext context, JsonElement parameters)
    {
        var query = NativeFunctions.Text(parameters, "query");
        var category = NativeFunctions.Text(parameters, "category");
        var limit = Math.Max(1, Math.Min(500, NativeFunctions.Integer(parameters, "limit", 50)));
        var filtered = Filter(query, category);
        var matches = (string.IsNullOrEmpty(query)
                ? filtered.OrderBy(proxy => proxy.Desc.Category).ThenBy(proxy => proxy.Desc.Name).ThenBy(Outdated)
                : filtered.OrderByDescending(proxy => string.Equals(proxy.Desc.Name, query, StringComparison.OrdinalIgnoreCase))
                    .ThenByDescending(proxy => string.Equals(proxy.Desc.NickName, query, StringComparison.OrdinalIgnoreCase))
                    .ThenBy(Outdated).ThenBy(proxy => proxy.Desc.Name))
            .Take(limit).ToArray();
        return new Dictionary<string, object?>
        {
            ["query"] = query,
            ["category"] = category,
            ["components"] = matches.Select(proxy => Summary(proxy, includeDescription: true)).ToArray(),
            ["count"] = matches.Length,
        };
    }

    [McpCommand("gh_batch_search_components", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object BatchSearchComponents(RequestContext context, JsonElement parameters)
    {
        var queries = StringArray(parameters, "queries");
        var maxMatches = Math.Max(1, Math.Min(50, NativeFunctions.Integer(parameters, "max_matches", 5)));
        var results = new Dictionary<string, object?>();
        var matchesByQuery = new Dictionary<string, object?>();
        var ambiguous = new Dictionary<string, object?>();
        var notFound = new List<string>();
        var foundCount = 0;
        foreach (var query in queries.Where(query => !string.IsNullOrWhiteSpace(query)))
        {
            var matches = GrasshopperHelpers.SearchProxies(query, null, maxMatches)
                .Select(proxy => Summary(proxy, includeDescription: false))
                .ToArray();
            results[query] = matches.FirstOrDefault();
            matchesByQuery[query] = matches;
            if (matches.Length == 0)
                notFound.Add(query);
            else
                foundCount++;
            if (matches.Length > 1)
                ambiguous[query] = matches;
        }
        return new Dictionary<string, object?>
        {
            ["results"] = results,
            ["matches"] = matchesByQuery,
            ["ambiguous"] = ambiguous,
            ["found_count"] = foundCount,
            ["total_queries"] = queries.Length,
            ["max_matches"] = maxMatches,
            ["not_found"] = notFound,
        };
    }

    [McpCommand("gh_list_component_categories", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object ListCategories(RequestContext context, JsonElement parameters)
    {
        var categories = GrasshopperHelpers.Proxies
            .Where(proxy => !string.IsNullOrEmpty(proxy.Desc.Category))
            .GroupBy(proxy => proxy.Desc.Category)
            .OrderBy(group => group.Key)
            .Select(group => new Dictionary<string, object?>
            {
                ["category"] = group.Key,
                ["subcategories"] = group
                    .Select(proxy => proxy.Desc.SubCategory ?? string.Empty)
                    .Where(name => name.Length > 0)
                    .Distinct()
                    .OrderBy(name => name)
                    .ToArray(),
                ["component_count"] = group.Count(),
            })
            .ToArray();
        return new Dictionary<string, object?>
        {
            ["categories"] = categories,
            ["category_count"] = categories.Length,
        };
    }

    [McpCommand("gh_get_available_components", ReadOnly = true, SupportsDryRun = false, CompletesWithinCallback = true)]
    internal static object GetAvailableComponents(RequestContext context, JsonElement parameters)
    {
        var category = NativeFunctions.Text(parameters, "category");
        var includeDescription = NativeFunctions.Boolean(parameters, "include_description");
        var limit = Math.Max(1, Math.Min(2000, NativeFunctions.Integer(parameters, "limit", 500)));
        var all = Filter(null, category).OrderBy(proxy => proxy.Desc.Category)
            .ThenBy(proxy => proxy.Desc.Name).ToArray();
        var selected = all.Take(limit).ToArray();
        return new Dictionary<string, object?>
        {
            ["components"] = selected.Select(proxy => Summary(proxy, includeDescription)).ToArray(),
            ["count"] = selected.Length,
            ["total_available"] = GrasshopperHelpers.Proxies.Count,
            ["category_filter"] = category,
            ["categories"] = all.Select(proxy => proxy.Desc.Category)
                .Where(name => !string.IsNullOrEmpty(name)).Distinct().OrderBy(name => name).ToArray(),
        };
    }

    [McpCommand("gh_get_component_type_info", ReadOnly = true, SupportsDryRun = false)]
    internal static object GetComponentTypeInfo(RequestContext context, JsonElement parameters)
    {
        return TypeInfo(parameters);
    }

    [McpCommand("gh_batch_get_component_type_info", ReadOnly = true, SupportsDryRun = false)]
    internal static object BatchGetComponentTypeInfo(RequestContext context, JsonElement parameters)
    {
        var components = NativeFunctions.Property(parameters, "components");
        if (components.ValueKind != JsonValueKind.Array || components.GetArrayLength() == 0)
        {
            throw new NativeCommandException("invalidParameters", "Component selectors must be an array.");
        }

        var notFound = new List<JsonElement>();
        var foundCount = 0;
        var results = components.EnumerateArray().Select(selector =>
        {
            if (selector.ValueKind != JsonValueKind.Object)
                throw new NativeCommandException("invalidParameters", "Each component selector must be an object.");
            var result = TypeInfo(selector);
            result["query"] = selector.Clone();
            if (result["success"] is true)
                foundCount++;
            else
                notFound.Add(selector.Clone());
            return result;
        }).ToArray();
        return new Dictionary<string, object?>
        {
            ["success"] = foundCount == results.Length,
            ["found_count"] = foundCount,
            ["total_queries"] = results.Length,
            ["results"] = results,
            ["not_found"] = notFound,
        };
    }

    private static Dictionary<string, object?> TypeInfo(JsonElement parameters)
    {
        var guid = NativeFunctions.Text(parameters, "guid")
            ?? NativeFunctions.Text(parameters, "component_guid");
        var name = NativeFunctions.Text(parameters, "name")
            ?? NativeFunctions.Text(parameters, "component_name");
        IGH_ObjectProxy proxy;
        try
        {
            proxy = GrasshopperHelpers.RequireProxy(name, guid);
        }
        catch (NativeCommandException error) when (error.Category == "componentTypeNotFound")
        {
            var term = name ?? guid ?? string.Empty;
            return new Dictionary<string, object?>
            {
                ["success"] = false,
                ["message"] = $"Component '{term}' not found",
                ["suggestions"] = GrasshopperHelpers.Proxies.Where(candidate =>
                    Contains(candidate.Desc.Name, term.Substring(0, Math.Min(3, term.Length))))
                    .Take(5).Select(candidate => candidate.Desc.Name).ToArray(),
            };
        }
        return GrasshopperHelpers.ComponentTypeInfo(proxy);
    }

    private static Dictionary<string, object?> Summary(IGH_ObjectProxy proxy, bool includeDescription)
    {
        var result = new Dictionary<string, object?>
        {
            ["guid"] = proxy.Guid.ToString("D"),
            ["name"] = proxy.Desc.Name,
            ["nickname"] = proxy.Desc.NickName,
            ["category"] = proxy.Desc.Category,
            ["subcategory"] = proxy.Desc.SubCategory,
            ["obsolete"] = proxy.Obsolete,
            ["hidden"] = proxy.Exposure == GH_Exposure.hidden,
        };
        if (includeDescription)
            result["description"] = proxy.Desc.Description;
        return result;
    }

    private static IEnumerable<IGH_ObjectProxy> Filter(string? query, string? category) =>
        GrasshopperHelpers.Proxies.Where(proxy => (string.IsNullOrEmpty(category) || Contains(proxy.Desc.Category, category))
            && (string.IsNullOrEmpty(query) || Contains(proxy.Desc.Name, query)
                || Contains(proxy.Desc.NickName, query) || Contains(proxy.Desc.Description, query)));

    private static bool Contains(string? value, string? term) =>
        term is not null && value?.IndexOf(term, StringComparison.OrdinalIgnoreCase) >= 0;

    private static bool Outdated(IGH_ObjectProxy proxy) => proxy.Obsolete || proxy.Exposure == GH_Exposure.hidden;

    private static string[] StringArray(JsonElement parameters, string key)
    {
        var values = NativeFunctions.Property(parameters, key);
        if (values.ValueKind != JsonValueKind.Array)
        {
            throw new NativeCommandException("invalidParameters", "Search queries must be an array.");
        }

        return values.EnumerateArray()
            .Select(value => value.ValueKind == JsonValueKind.String ? value.GetString() ?? string.Empty : string.Empty)
            .ToArray();
    }
}
