using System.Text.Json;
using System.Text.Json.Nodes;

namespace RhinoMcpBridge.Serializers;

internal static class Serializer
{
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = false,
    };

    public static JsonNode? NormalizeNegativeZero(JsonNode? value)
    {
        if (value is JsonValue scalar && scalar.TryGetValue<double>(out var number))
        {
            return number == 0d ? JsonValue.Create(0d) : value.DeepClone();
        }

        if (value is JsonArray array)
        {
            var normalized = new JsonArray();
            foreach (var item in array)
            {
                normalized.Add(NormalizeNegativeZero(item));
            }

            return normalized;
        }

        if (value is JsonObject objectValue)
        {
            var normalized = new JsonObject();
            foreach (var entry in objectValue)
            {
                normalized[entry.Key] = NormalizeNegativeZero(entry.Value);
            }

            return normalized;
        }

        return value?.DeepClone();
    }

    public static byte[] Serialize(object? value)
    {
        var node = JsonSerializer.SerializeToNode(value, Options);
        return JsonSerializer.SerializeToUtf8Bytes(NormalizeNegativeZero(node), Options);
    }
}
