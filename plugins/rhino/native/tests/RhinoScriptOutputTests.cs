using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class RhinoScriptOutputTests
{
    [Fact]
    public void Python_print_echoes_are_removed_in_order_without_removing_command_output()
    {
        using var native = NativeTestAssembly.Load();
        var type = native.Assembly.GetType("RhinoMcpBridge.Functions.ExecuteRhinoscript", throwOnError: true)!;
        var deduplicate = type.GetMethod("WithoutEchoedPrints", BindingFlags.Static | BindingFlags.NonPublic)!;
        var result = (string[])deduplicate.Invoke(null,
            [new[] { "Command: _Length", "first\r\n", "Length = 2", "second", "second" }, "first\r\nsecond\n"])!;
        Assert.Equal(new[] { "Command: _Length", "Length = 2", "second" }, result);
    }
}
