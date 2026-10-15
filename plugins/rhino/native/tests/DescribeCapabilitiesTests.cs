using System.Reflection;
using System.Text.Json;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class DescribeCapabilitiesTests
{
    [Fact]
    public void Capability_response_uses_bridge_version_and_structured_perception_flags()
    {
        using var native = NativeTestAssembly.Load();
        var type = native.Assembly.GetType("RhinoMcpBridge.Functions.DescribeCapabilities", throwOnError: true)!;
        var method = type.GetMethod("Describe", BindingFlags.Static | BindingFlags.NonPublic)!;
        using var parameters = JsonDocument.Parse("{}");
        var response = method.Invoke(null, new object?[] { null, parameters.RootElement });
        using var json = JsonDocument.Parse(JsonSerializer.Serialize(response));
        var root = json.RootElement;

        Assert.Equal(native.Assembly.GetName().Version!.ToString(3), root.GetProperty("version").GetString());
        Assert.Equal(root.GetProperty("commands").GetArrayLength(), root.GetProperty("command_count").GetInt32());
        Assert.True(root.GetProperty("command_count").GetInt32() >= 60);
        var flags = root.GetProperty("perception").GetProperty("envelope_flags");
        Assert.Equal(2, flags.GetArrayLength());
        Assert.Equal("include_delta", flags[0].GetProperty("flag").GetString());
        Assert.Equal("_delta", flags[0].GetProperty("attaches").GetString());
        Assert.Equal("include_health", flags[1].GetProperty("flag").GetString());
        Assert.Equal("_health", flags[1].GetProperty("attaches").GetString());
    }
}
