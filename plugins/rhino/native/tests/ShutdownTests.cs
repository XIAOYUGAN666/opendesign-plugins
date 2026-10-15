using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class ShutdownTests
{
    [Fact]
    public async Task Stopping_while_waiting_for_desktop_cancels_background_retries_without_blocking()
    {
        using var native = NativeTestAssembly.Load();
        var registration = BridgeTestSupport.CreateRegistration(native);
        var controller = BridgeTestSupport.CreateBridgeController(native, registration);
        var start = controller.GetType().GetMethod(
            "Start",
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
        );
        Assert.NotNull(start);
        var runTask = (Task?)start!.Invoke(controller, null);
        Assert.NotNull(runTask);

        await Task.Delay(25, TestContext.Current.CancellationToken);
        var stopwatch = System.Diagnostics.Stopwatch.StartNew();
        BridgeTestSupport.Invoke(controller, "Stop");
        stopwatch.Stop();

        Assert.True(stopwatch.Elapsed < TimeSpan.FromMilliseconds(250));
        await runTask!.WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
    }

    [Fact]
    public void Rhino_plugin_overrides_shutdown_and_does_not_wait_synchronously()
    {
        using var native = NativeTestAssembly.Load();
        var pluginType = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.RhinoMcpBridgePlugin");
        var shutdown = pluginType.GetMethod(
            "OnShutdown",
            BindingFlags.Instance | BindingFlags.NonPublic
        );

        Assert.NotNull(shutdown);
        Assert.Equal(pluginType, shutdown!.DeclaringType);
        Assert.Empty(shutdown.GetParameters());
        Assert.Equal(typeof(void), shutdown.ReturnType);

        var pluginSource = File.ReadAllText(Path.Combine(native.PluginRoot, "native", "RhinoMcpBridgePlugin.cs"));
        Assert.Contains("_controller?.Stop()", pluginSource, StringComparison.Ordinal);
        Assert.DoesNotContain(".Wait()", pluginSource, StringComparison.Ordinal);
        Assert.DoesNotContain("GetAwaiter().GetResult()", pluginSource, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Disposing_the_bridge_closes_the_authenticated_provider_socket()
    {
        using var native = NativeTestAssembly.Load();
        var server = CreateActiveServer(native);
        var port = Convert.ToInt32(BridgeTestSupport.Property(server, "Port"));
        using var client = await BridgeTestSupport.ConnectLoopbackAsync(port);
        await BridgeTestSupport.WriteFrameAsync(client.GetStream(), BridgeTestSupport.AuthenticationJson());
        using var response = await BridgeTestSupport.ReadFrameAsync(client.GetStream());
        Assert.NotNull(response);
        Assert.Equal("authenticated", response!.RootElement.GetProperty("type").GetString());

        ((IDisposable)server).Dispose();

        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        var bytes = new byte[1];
        var read = await client.GetStream().ReadAsync(bytes, 0, 1, timeout.Token);
        Assert.Equal(0, read);
    }

    private static object CreateActiveServer(NativeTestAssembly native)
    {
        var lease = BridgeTestSupport.CreateLease(native);
        var executor = new Func<string, string, bool, bool, CancellationToken, Task<byte[]>>(
            (_, _, _, _, _) => Task.FromResult(System.Text.Encoding.UTF8.GetBytes("{}"))
        );
        var server = BridgeTestSupport.CreateBridgeServer(native, lease, executor);
        BridgeTestSupport.Invoke(server, "Start");
        return server;
    }
}
