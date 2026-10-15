using System.Text;
using System.Text.Json;
using Grasshopper;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Data;
using Grasshopper.Kernel.Special;
using Grasshopper.Kernel.Types;
using Rhino.Geometry;

namespace RhinoMcpBridge.Functions;

internal static class GrasshopperHelpers
{
    internal static GH_Document RequireDocument(RequestContext context) =>
        context.RequireGrasshopperDocument();

    internal static IReadOnlyList<IGH_ObjectProxy> Proxies =>
        Instances.ComponentServer?.ObjectProxies
            .Where(proxy => proxy is not null && !proxy.Obsolete)
            .ToArray()
        ?? Array.Empty<IGH_ObjectProxy>();

    internal static IReadOnlyList<IGH_ObjectProxy> SearchProxies(
        string? query,
        string? category,
        int limit
    )
    {
        var boundedLimit = Math.Max(1, Math.Min(limit, 5000));
        IEnumerable<IGH_ObjectProxy> candidates;
        if (string.IsNullOrWhiteSpace(query))
        {
            candidates = Proxies;
        }
        else
        {
            var componentServer = Instances.ComponentServer;
            if (componentServer is null)
            {
                return Array.Empty<IGH_ObjectProxy>();
            }

            IGH_ObjectProxy[] matches = Array.Empty<IGH_ObjectProxy>();
            double[] scores = Array.Empty<double>();
            var count = componentServer.FindObjects(
                query!.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries),
                boundedLimit,
                ref matches,
                ref scores
            );
            candidates = matches.Take(Math.Max(0, Math.Min(count, matches.Length)));
        }

        var seen = new HashSet<Guid>();
        return candidates
            .Where(proxy => proxy is not null && !proxy.Obsolete)
            .Where(proxy => string.IsNullOrWhiteSpace(category)
                || string.Equals(proxy.Desc.Category, category, StringComparison.OrdinalIgnoreCase))
            .Where(proxy => seen.Add(proxy.Guid))
            .Take(boundedLimit)
            .ToArray();
    }

    internal static IGH_ObjectProxy RequireProxy(string? name, string? guid)
    {
        var proxies = Proxies;
        if (!string.IsNullOrWhiteSpace(guid))
        {
            if (!Guid.TryParse(guid, out var id))
            {
                throw new NativeCommandException("invalidParameters", "The Grasshopper component identifier is invalid.");
            }

            return proxies.FirstOrDefault(proxy => proxy.Guid == id)
                ?? throw new NativeCommandException("componentTypeNotFound", "The Grasshopper component type was not found.");
        }

        if (string.IsNullOrWhiteSpace(name))
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper component name or identifier is required.");
        }

        var matches = proxies
            .Where(proxy => string.Equals(proxy.Desc.Name, name, StringComparison.OrdinalIgnoreCase)
                || string.Equals(proxy.Desc.NickName, name, StringComparison.OrdinalIgnoreCase)
                || string.Equals(proxy.Desc.Category + " "+ proxy.Desc.SubCategory + " "+ proxy.Desc.Name, name, StringComparison.OrdinalIgnoreCase))
            .Take(2)
            .ToArray();
        return matches.Length switch
        {
            1 => matches[0],
            0 => throw new NativeCommandException("componentTypeNotFound", "The Grasshopper component type was not found."),
            _ => throw new NativeCommandException("ambiguousComponentType", "The component name is ambiguous; search and use its component identifier."),
        };
    }

    internal static IGH_DocumentObject CreateInstance(IGH_ObjectProxy proxy) =>
        proxy.CreateInstance()
        ?? throw new NativeCommandException("componentCreateFailed", "Grasshopper could not create the requested component instance.");

    internal static IGH_DocumentObject RequireObject(
        GH_Document document,
        JsonElement parameters,
        string graphId = "",
        string idKey = "instance_id",
        string nicknameKey = "nickname"
    )
    {
        if (NativeFunctions.Has(parameters, idKey))
        {
            var text = NativeFunctions.Text(parameters, idKey);
            if (!Guid.TryParse(text, out var id))
            {
                throw new NativeCommandException("invalidParameters", "The Grasshopper instance identifier is invalid.");
            }

            return document.Objects.FirstOrDefault(item => item.InstanceGuid == id)
                ?? throw new NativeCommandException("componentNotFound", "The Grasshopper canvas object was not found.");
        }

        var selector = NativeFunctions.Text(parameters, nicknameKey);
        if (selector is null)
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper instance identifier or nickname is required.");
        }
        selector = selector.Trim();
        if (selector.Length == 0)
        {
            throw new NativeCommandException("invalidParameters", "A Grasshopper instance identifier or nickname is required.");
        }

        if (!string.IsNullOrWhiteSpace(graphId))
        {
            var aliasGuid = document.ValueTable.GetValue(AliasKey(graphId, selector), string.Empty);
            if (Guid.TryParse(aliasGuid, out var resolvedId))
            {
                var aliasObject = document.Objects.FirstOrDefault(item => item.InstanceGuid == resolvedId);
                if (aliasObject is not null)
                {
                    return aliasObject;
                }
            }
        }

        var matches = document.Objects
            .Where(item => string.Equals(item.NickName, selector, StringComparison.OrdinalIgnoreCase)
                || string.Equals(item.Name, selector, StringComparison.OrdinalIgnoreCase))
            .Take(2)
            .ToArray();
        return matches.Length switch
        {
            1 => matches[0],
            0 => throw new NativeCommandException("componentNotFound", "The Grasshopper canvas object was not found."),
            _ => throw new NativeCommandException("ambiguousComponentName", "The canvas nickname is not unique; use its instance identifier."),
        };
    }

    internal static GH_Component RequireComponent(
        GH_Document document,
        JsonElement parameters,
        string graphId = "",
        string idKey = "instance_id",
        string nicknameKey = "nickname"
    ) => RequireObject(document, parameters, graphId, idKey, nicknameKey) as GH_Component
        ?? throw new NativeCommandException("componentNotFound", "The selected Grasshopper object is not a component.");

    internal static IGH_Param[] Inputs(IGH_DocumentObject item) => item switch
    {
        GH_Component component => component.Params.Input.ToArray(),
        IGH_Param parameter => new[] { parameter },
        _ => Array.Empty<IGH_Param>(),
    };

    internal static IGH_Param[] Outputs(IGH_DocumentObject item) => item switch
    {
        GH_Component component => component.Params.Output.ToArray(),
        IGH_Param parameter => new[] { parameter },
        _ => Array.Empty<IGH_Param>(),
    };

    internal static IGH_Param RequirePort(
        IReadOnlyList<IGH_Param> ports,
        JsonElement parameters,
        string indexKey,
        string nameKey
    )
    {
        if (NativeFunctions.Has(parameters, indexKey))
        {
            var index = NativeFunctions.Integer(parameters, indexKey, -1);
            if (index < 0 || index >= ports.Count)
            {
                throw new NativeCommandException("parameterNotFound", "The requested Grasshopper parameter index is out of range.");
            }

            return ports[index];
        }

        var name = NativeFunctions.Text(parameters, nameKey);
        if (string.IsNullOrWhiteSpace(name))
        {
            if (ports.Count == 1)
            {
                return ports[0];
            }

            throw new NativeCommandException("invalidParameters", "A Grasshopper parameter index or name is required.");
        }

        var matches = ports
            .Where(port => string.Equals(port.Name, name, StringComparison.OrdinalIgnoreCase)
                || string.Equals(port.NickName, name, StringComparison.OrdinalIgnoreCase))
            .Take(2)
            .ToArray();
        return matches.Length switch
        {
            1 => matches[0],
            0 => throw new NativeCommandException("parameterNotFound", "The requested Grasshopper parameter was not found."),
            _ => throw new NativeCommandException("ambiguousParameterName", "More than one Grasshopper parameter has the requested name."),
        };
    }

    internal static object ComponentTypeInfo(IGH_ObjectProxy proxy, bool includeDescription = true)
    {
        var instance = CreateInstance(proxy);
        var component = instance as GH_Component;
        return new Dictionary<string, object?>
        {
            ["guid"] = proxy.Guid.ToString("D"),
            ["name"] = proxy.Desc.Name,
            ["nickname"] = proxy.Desc.NickName,
            ["category"] = proxy.Desc.Category,
            ["subcategory"] = proxy.Desc.SubCategory,
            ["description"] = includeDescription ? proxy.Desc.Description : null,
            ["obsolete"] = proxy.Obsolete,
            ["inputs"] = component is null ? Array.Empty<object>() : component.Params.Input.Select(port => PortInfo(port)).ToArray(),
            ["outputs"] = component is null ? Array.Empty<object>() : component.Params.Output.Select(port => PortInfo(port)).ToArray(),
        };
    }

    internal static object ObjectInfo(IGH_DocumentObject item, bool includeValues = false, int maxItems = 20)
    {
        var attributes = item.Attributes;
        if (attributes is null)
        {
            item.CreateAttributes();
            attributes = item.Attributes;
        }

        if (attributes is null)
        {
            throw new NativeCommandException("componentInfoUnavailable", "Grasshopper could not create canvas attributes for the selected object.");
        }

        var inputs = Inputs(item);
        var outputs = Outputs(item);
        var active = item as IGH_ActiveObject;
        var preview = item as IGH_PreviewObject;
        return new Dictionary<string, object?>
        {
            ["instance_id"] = item.InstanceGuid.ToString("D"),
            ["name"] = item.Name,
            ["nickname"] = item.NickName,
            ["category"] = item.Category,
            ["subcategory"] = item.SubCategory,
            ["component_guid"] = item.ComponentGuid.ToString("D"),
            ["position"] = new[] { attributes.Pivot.X, attributes.Pivot.Y },
            ["inputs"] = inputs.Select((port, index) => PortInfo(port, index, includeValues, maxItems)).ToArray(),
            ["outputs"] = outputs.Select((port, index) => PortInfo(port, index, includeValues, maxItems)).ToArray(),
            ["enabled"] = active?.Locked != true,
            ["preview"] = preview is { Hidden: false },
            ["runtime_messages"] = RuntimeMessages(active),
        };
    }

    private static object[] RuntimeMessages(IGH_ActiveObject? active)
    {
        if (active is null)
        {
            return Array.Empty<object>();
        }

        return new[]
            {
                GH_RuntimeMessageLevel.Remark,
                GH_RuntimeMessageLevel.Warning,
                GH_RuntimeMessageLevel.Error,
            }
            .SelectMany(level => active.RuntimeMessages(level).Select(message => (object)new Dictionary<string, object?>
            {
                ["level"] = level.ToString(),
                ["message"] = message,
            }))
            .ToArray();
    }

    internal static object PortInfo(IGH_Param parameter, int index = -1, bool includeValues = false, int maxItems = 20)
    {
        var result = new Dictionary<string, object?>
        {
            ["index"] = index,
            ["name"] = parameter.Name,
            ["nickname"] = parameter.NickName,
            ["type"] = parameter.TypeName,
            ["optional"] = parameter.Optional,
            ["access"] = parameter.Access.ToString(),
            ["source_count"] = parameter.SourceCount,
            ["recipient_count"] = parameter.Recipients.Count,
        };
        if (includeValues)
        {
            result["data"] = DataTree(parameter, maxItems);
        }

        return result;
    }

    internal static object DataTree(IGH_Param parameter, int maxItems)
    {
        var limit = Math.Max(0, Math.Min(maxItems, 1000));
        var result = new List<object?>();
        var consumed = 0;
        var tree = parameter.VolatileData;
        foreach (var path in tree.Paths)
        {
            var values = new List<object?>();
            foreach (var goo in tree.get_Branch(path))
            {
                if (consumed >= limit)
                {
                    break;
                }

                values.Add(SerializeBranchItem(goo));
                consumed++;
            }

            result.Add(new Dictionary<string, object?>
            {
                ["path"] = path.ToString(),
                ["values"] = values,
            });
            if (consumed >= limit)
            {
                break;
            }
        }

        return new Dictionary<string, object?>
        {
            ["branches"] = result,
            ["item_count"] = parameter.VolatileDataCount,
            ["truncated"] = parameter.VolatileDataCount > consumed,
        };
    }

    internal static object? SerializeGoo(IGH_Goo? goo)
    {
        if (goo is null || !goo.IsValid)
        {
            return null;
        }

        object? value;
        try
        {
            value = goo.ScriptVariable();
        }
        catch
        {
            return goo.ToString();
        }

        return value switch
        {
            null => null,
            string or bool or byte or short or int or long or float or double or decimal => value,
            Guid id => id.ToString("D"),
            Point3d point => NativeFunctions.PointArray(point),
            Vector3d vector => new[] { vector.X, vector.Y, vector.Z },
            GeometryBase geometry => new Dictionary<string, object?>
            {
                ["type"] = NativeFunctions.GeometryType(geometry),
                ["is_valid"] = geometry.IsValid,
                ["bounding_box"] = NativeFunctions.BoundingBox(geometry.GetBoundingBox(accurate: true)),
            },
            System.Collections.IEnumerable values when value is not string => values.Cast<object?>().Take(100).Select(SerializeObject).ToArray(),
            _ => SerializeObject(value),
        };
    }

    internal static object? SerializeBranchItem(object? item) =>
        item is IGH_Goo goo ? SerializeGoo(goo) : SerializeObject(item);

    internal static object? SerializeObject(object? value) => value switch
    {
        null => null,
        string or bool or byte or short or int or long or float or double or decimal => value,
        Guid id => id.ToString("D"),
        Point3d point => NativeFunctions.PointArray(point),
        Vector3d vector => new[] { vector.X, vector.Y, vector.Z },
        GeometryBase geometry => new Dictionary<string, object?>
        {
            ["type"] = NativeFunctions.GeometryType(geometry),
            ["is_valid"] = geometry.IsValid,
            ["bounding_box"] = NativeFunctions.BoundingBox(geometry.GetBoundingBox(accurate: true)),
        },
        _ => value.ToString(),
    };

    internal static string RequireGraphId(JsonElement value)
    {
        var graphId = NativeFunctions.Text(value, "graph_id")?.Trim();
        if (graphId is null || graphId.Length == 0 || graphId.Length > 128)
        {
            throw new NativeCommandException("invalidParameters", "A graph identifier between 1 and 128 characters is required.");
        }

        return graphId;
    }

    internal static string AliasKey(string graphId, string alias) =>
        GraphPrefix(graphId) + "alias." + Token(alias);

    internal static string GroupKey(string graphId, string? alias = null) =>
        GraphPrefix(graphId)
        + "group"
        + (alias is null || string.IsNullOrWhiteSpace(alias) ? string.Empty : "." + Token(alias));

    internal static string RoleKey(string graphId, string alias) => GraphPrefix(graphId) + "role." + Token(alias);

    internal static string GraphPrefix(string graphId) =>
        "rhino_mcp.graph." + Token(graphId) + ".";

    internal static void StoreAlias(GH_Document document, string graphId, string alias, Guid instanceId) =>
        document.ValueTable.SetValue(AliasKey(graphId, alias), instanceId.ToString("D"));

    internal static void StoreGraphGroup(GH_Document document, string graphId, Guid groupId, string? alias = null) =>
        document.ValueTable.SetValue(GroupKey(graphId, alias), groupId.ToString("D"));

    internal static void StoreGraphRole(GH_Document document, string graphId, string alias, string role) =>
        document.ValueTable.SetValue(RoleKey(graphId, alias), role);

    internal static string? GraphRole(GH_Document document, string graphId, string alias)
    {
        var value = document.ValueTable.GetValue(RoleKey(graphId, alias), string.Empty);
        return value.Length == 0 ? null : value;
    }

    internal static IReadOnlyList<GH_Group> FindGraphGroups(GH_Document document, string graphId)
    {
        var prefix = GraphPrefix(graphId) + "group";
        var ids = document.ValueTable.EntryNames()
            .Where(name => string.Equals(name, prefix, StringComparison.Ordinal) || name.StartsWith(prefix + ".", StringComparison.Ordinal))
            .Select(name => document.ValueTable.GetValue(name, string.Empty))
            .Select(value => Guid.TryParse(value, out var id) ? id : Guid.Empty)
            .Where(id => id != Guid.Empty)
            .ToHashSet();
        return document.Objects.OfType<GH_Group>().Where(group => ids.Contains(group.InstanceGuid)).ToArray();
    }

    internal static GH_Group? FindGraphGroup(GH_Document document, string graphId) =>
        FindGraphGroups(document, graphId).FirstOrDefault();

    internal static IReadOnlyList<IGH_DocumentObject> GraphObjects(GH_Document document, string graphId)
    {
        var prefix = GraphPrefix(graphId) + "alias.";
        var ids = document.ValueTable.EntryNames()
            .Where(name => name.StartsWith(prefix, StringComparison.Ordinal))
            .Select(name => document.ValueTable.GetValue(name, string.Empty))
            .Select(value => Guid.TryParse(value, out var id) ? id : Guid.Empty)
            .Where(id => id != Guid.Empty)
            .ToHashSet();
        return document.Objects.Where(item => ids.Contains(item.InstanceGuid)).ToArray();
    }

    internal static IReadOnlyDictionary<string, IGH_DocumentObject> GraphAliases(
        GH_Document document,
        string graphId
    )
    {
        var prefix = GraphPrefix(graphId) + "alias.";
        var aliases = new Dictionary<string, IGH_DocumentObject>(StringComparer.Ordinal);
        foreach (var name in document.ValueTable.EntryNames().Where(name => name.StartsWith(prefix, StringComparison.Ordinal)))
        {
            var token = name.Substring(prefix.Length);
            var alias = DecodeToken(token);
            var idText = document.ValueTable.GetValue(name, string.Empty);
            if (Guid.TryParse(idText, out var id))
            {
                var item = document.Objects.FirstOrDefault(candidate => candidate.InstanceGuid == id);
                if (item is not null)
                {
                    aliases[alias] = item;
                }
            }
        }

        return aliases;
    }

    internal static void ForgetGraph(GH_Document document, string graphId)
    {
        var prefix = GraphPrefix(graphId);
        foreach (var name in document.ValueTable.EntryNames().Where(name => name.StartsWith(prefix, StringComparison.Ordinal)).ToArray())
        {
            document.ValueTable.DeleteValue(name);
        }
    }

    private static string Token(string value) =>
        Convert.ToBase64String(Encoding.UTF8.GetBytes(value))
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');

    private static string DecodeToken(string value)
    {
        var base64 = value.Replace('-', '+').Replace('_', '/');
        switch (base64.Length % 4)
        {
            case 2:
                base64 += "==";
                break;
            case 3:
                base64 += "=";
                break;
        }

        try
        {
            return Encoding.UTF8.GetString(Convert.FromBase64String(base64));
        }
        catch (FormatException)
        {
            return string.Empty;
        }
    }
}
