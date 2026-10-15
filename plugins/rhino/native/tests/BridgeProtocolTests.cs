using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class BridgeProtocolTests
{
    [Fact]
    public async Task Listener_binds_ipv4_loopback_on_port_zero_and_only_reports_port_after_bind()
    {
        using var native = NativeTestAssembly.Load();
        using var server = (IDisposable)CreateServer(native);
        Assert.Equal(0, Convert.ToInt32(BridgeTestSupport.Property(server, "Port")));

        BridgeTestSupport.Invoke(server, "Start");
        var port = Convert.ToInt32(BridgeTestSupport.Property(server, "Port"));

        Assert.InRange(port, 1, 65535);
        using var client = await BridgeTestSupport.ConnectLoopbackAsync(port);
    }

    [Fact]
    public async Task Authentication_must_be_the_first_frame_before_any_command()
    {
        using var native = NativeTestAssembly.Load();
        var dispatchCount = 0;
        using var server = (IDisposable)CreateServer(
            native,
            () => Interlocked.Increment(ref dispatchCount)
        );
        BridgeTestSupport.Invoke(server, "Start");
        using var client = await BridgeTestSupport.ConnectLoopbackAsync(Port(server));
        await BridgeTestSupport.WriteFrameAsync(
            client.GetStream(),
            """{"type":"get_document_summary","params":{}}"""
        );

        using var response = await ReadWithTimeoutAsync(client.GetStream());

        Assert.NotNull(response);
        Assert.Equal("authenticationRejected", response!.RootElement.GetProperty("type").GetString());
        Assert.Equal(0, Volatile.Read(ref dispatchCount));
    }

    [Theory]
    [InlineData("zero")]
    [InlineData("oversized")]
    [InlineData("invalid-utf8")]
    [InlineData("invalid-json")]
    public async Task Invalid_frame_sizes_utf8_and_json_are_rejected(string frameKind)
    {
        using var native = NativeTestAssembly.Load();
        var dispatchCount = 0;
        using var server = (IDisposable)CreateServer(
            native,
            () => Interlocked.Increment(ref dispatchCount)
        );
        BridgeTestSupport.Invoke(server, "Start");
        using var client = await BridgeTestSupport.ConnectLoopbackAsync(Port(server));
        var stream = client.GetStream();
        switch (frameKind)
        {
            case "zero":
                await stream.WriteAsync(
                    new byte[] { 0, 0, 0, 0 },
                    0,
                    4,
                    TestContext.Current.CancellationToken
                );
                break;
            case "oversized":
                await stream.WriteAsync(
                    new byte[] { 4, 0, 0, 1 },
                    0,
                    4,
                    TestContext.Current.CancellationToken
                );
                break;
            case "invalid-utf8":
                await BridgeTestSupport.WriteRawFrameAsync(stream, new byte[] { 0xFF });
                break;
            case "invalid-json":
                await BridgeTestSupport.WriteRawFrameAsync(stream, Encoding.UTF8.GetBytes("{"));
                break;
        }

        using var response = await ReadWithTimeoutAsync(stream);

        Assert.Null(response);
        Assert.Equal(0, Volatile.Read(ref dispatchCount));
    }

    [Theory]
    [InlineData("generation")]
    [InlineData("token")]
    [InlineData("providerBuildId")]
    [InlineData("contractDigest")]
    public async Task Stale_or_mismatched_bridge_authentication_is_rejected_without_details(string field)
    {
        using var native = NativeTestAssembly.Load();
        using var server = (IDisposable)CreateServer(native);
        BridgeTestSupport.Invoke(server, "Start");
        using var client = await BridgeTestSupport.ConnectLoopbackAsync(Port(server));
        var auth = field switch
        {
            "generation" => BridgeTestSupport.AuthenticationJson(generation: 2),
            "token" => BridgeTestSupport.AuthenticationJson(token: "wrong-bridge-token-0123456789"),
            "providerBuildId" => BridgeTestSupport.AuthenticationJson(providerBuildId: "sha256:1111111111111111111111111111111111111111111111111111111111111111"),
            _ => BridgeTestSupport.AuthenticationJson(contractDigest: "sha256:1111111111111111111111111111111111111111111111111111111111111111"),
        };
        await BridgeTestSupport.WriteFrameAsync(client.GetStream(), auth);

        using var response = await ReadWithTimeoutAsync(client.GetStream());

        Assert.NotNull(response);
        Assert.Equal(new[] { "type" }, response!.RootElement.EnumerateObject().Select(property => property.Name));
        Assert.Equal("authenticationRejected", response.RootElement.GetProperty("type").GetString());
    }

    [Fact]
    public async Task Authenticated_commands_are_dispatched_once_and_return_one_strict_response()
    {
        using var native = NativeTestAssembly.Load();
        var dispatchCount = 0;
        string? dispatchedName = null;
        using var server = (IDisposable)CreateServer(
            native,
            () => Interlocked.Increment(ref dispatchCount),
            (name, _, includeDelta, includeHealth, _) =>
            {
                dispatchedName = name;
                Assert.True(includeDelta);
                Assert.False(includeHealth);
                return Task.FromResult(Encoding.UTF8.GetBytes("""{"answer":42}"""));
            }
        );
        BridgeTestSupport.Invoke(server, "Start");
        using var client = await BridgeTestSupport.ConnectLoopbackAsync(Port(server));
        var stream = client.GetStream();
        await BridgeTestSupport.WriteFrameAsync(stream, BridgeTestSupport.AuthenticationJson());
        using var authResponse = await BridgeTestSupport.ReadFrameAsync(stream);
        Assert.NotNull(authResponse);
        Assert.Equal("authenticated", authResponse!.RootElement.GetProperty("type").GetString());

        await BridgeTestSupport.WriteFrameAsync(
            stream,
            """{"type":"describe_capabilities","params":{},"include_delta":true}"""
        );
        using var commandResponse = await BridgeTestSupport.ReadFrameAsync(stream);

        Assert.NotNull(commandResponse);
        Assert.Equal("success", commandResponse!.RootElement.GetProperty("status").GetString());
        Assert.Equal(42, commandResponse.RootElement.GetProperty("result").GetProperty("answer").GetInt32());
        Assert.Equal("describe_capabilities", dispatchedName);
        Assert.Equal(1, Volatile.Read(ref dispatchCount));
    }

    [Fact]
    public async Task Rejects_a_second_provider_socket_while_the_first_is_active()
    {
        using var native = NativeTestAssembly.Load();
        using var server = (IDisposable)CreateServer(native);
        BridgeTestSupport.Invoke(server, "Start");
        using var first = await BridgeTestSupport.ConnectLoopbackAsync(Port(server));
        await BridgeTestSupport.WriteFrameAsync(first.GetStream(), BridgeTestSupport.AuthenticationJson());
        using var firstResponse = await BridgeTestSupport.ReadFrameAsync(first.GetStream());
        Assert.NotNull(firstResponse);
        Assert.Equal("authenticated", firstResponse!.RootElement.GetProperty("type").GetString());

        using var second = await BridgeTestSupport.ConnectLoopbackAsync(Port(server));
        await BridgeTestSupport.WriteFrameAsync(second.GetStream(), BridgeTestSupport.AuthenticationJson());
        using var secondResponse = await ReadWithTimeoutAsync(second.GetStream());

        Assert.NotNull(secondResponse);
        Assert.Equal("authenticationRejected", secondResponse!.RootElement.GetProperty("type").GetString());
        Assert.True(first.Connected);
    }

    private static object CreateServer(
        NativeTestAssembly native,
        Action? onDispatch = null,
        Func<string, string, bool, bool, CancellationToken, Task<byte[]>>? executor = null
    )
    {
        var lease = BridgeTestSupport.CreateLease(native);
        executor ??= (_, _, _, _, _) => Task.FromResult(Encoding.UTF8.GetBytes("{}"));
        var trackingExecutor = new Func<string, string, bool, bool, CancellationToken, Task<byte[]>>(
            async (name, parameters, includeDelta, includeHealth, cancellationToken) =>
            {
                onDispatch?.Invoke();
                return await executor(name, parameters, includeDelta, includeHealth, cancellationToken);
            }
        );
        return BridgeTestSupport.CreateBridgeServer(native, lease, trackingExecutor);
    }

    private static int Port(object server) => Convert.ToInt32(BridgeTestSupport.Property(server, "Port"));

    private static async Task<JsonDocument?> ReadWithTimeoutAsync(Stream stream)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        return await BridgeTestSupport.ReadFrameAsync(new CancellableStream(stream, timeout.Token));
    }

    private sealed class CancellableStream(Stream inner, CancellationToken cancellationToken) : Stream
    {
        public override bool CanRead => inner.CanRead;
        public override bool CanSeek => false;
        public override bool CanWrite => inner.CanWrite;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Flush() => inner.Flush();
        public override int Read(byte[] buffer, int offset, int count) => inner.Read(buffer, offset, count);
        public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken token) =>
            inner.ReadAsync(buffer, offset, count, cancellationToken);
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => inner.Write(buffer, offset, count);
    }
}
