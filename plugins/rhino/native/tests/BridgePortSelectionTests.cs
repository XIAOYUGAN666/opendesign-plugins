using System.Net;
using System.Net.Sockets;
using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class BridgePortSelectionTests
{
    [Fact]
    public void Explicit_port_conflict_does_not_fall_back_and_environment_supplies_port_when_unset()
    {
        using var native = NativeTestAssembly.Load();
        var open = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.BridgePortSelection")
            .GetMethod("Open", BindingFlags.Static | BindingFlags.NonPublic)!;
        var occupied = new TcpListener(IPAddress.Loopback, 0) { ExclusiveAddressUse = true };
        occupied.Start();
        try
        {
            var port = ((IPEndPoint)occupied.LocalEndpoint).Port;
            var conflict = Assert.Throws<TargetInvocationException>(() => open.Invoke(null, [port, "1999"]));
            Assert.IsType<SocketException>(conflict.InnerException);
        }
        finally
        {
            occupied.Stop();
        }

        var spare = new TcpListener(IPAddress.Loopback, 0);
        spare.Start();
        var environmentPort = ((IPEndPoint)spare.LocalEndpoint).Port;
        spare.Stop();
        using var binding = (IDisposable)open.Invoke(null, [0, environmentPort.ToString()])!;
        Assert.Equal(environmentPort, BridgeTestSupport.Property(binding, "Port"));
        Assert.Equal("RHINO_MCP_PORT", BridgeTestSupport.Property(binding, "Source"));
    }

    [Fact]
    public void Automatic_listeners_keep_distinct_ports_while_both_are_open()
    {
        using var native = NativeTestAssembly.Load();
        var open = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.BridgePortSelection")
            .GetMethod("Open", BindingFlags.Static | BindingFlags.NonPublic)!;
        using var first = (IDisposable)open.Invoke(null, [0, null])!;
        using var second = (IDisposable)open.Invoke(null, [0, null])!;
        var firstPort = Assert.IsType<int>(BridgeTestSupport.Property(first, "Port"));
        var secondPort = Assert.IsType<int>(BridgeTestSupport.Property(second, "Port"));
        Assert.InRange(firstPort, 1999, 2008);
        Assert.InRange(secondPort, firstPort + 1, 2008);
    }
}
