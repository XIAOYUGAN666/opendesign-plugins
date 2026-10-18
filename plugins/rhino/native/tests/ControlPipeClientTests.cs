using System.IO.Pipes;
using System.Reflection;
using System.Text.Json;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class ControlPipeClientTests
{
    [Fact]
    public void Uses_the_fixed_local_current_user_asynchronous_pipe()
    {
        using var native = NativeTestAssembly.Load();
        var clientType = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.ControlPipeClient");
        var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;

        Assert.Equal("rhino-mcp-control-v1", clientType.GetField("PipeName", flags)?.GetRawConstantValue());
        Assert.Equal(".", clientType.GetField("ServerName", flags)?.GetRawConstantValue());
        var options = (PipeOptions?)clientType.GetField("PipeOptions", flags)?.GetValue(null);
        Assert.Equal(PipeOptions.CurrentUserOnly | PipeOptions.Asynchronous, options);
    }

    [Fact]
    public async Task Sends_exact_registration_and_accepts_only_a_valid_control_lease()
    {
        using var native = NativeTestAssembly.Load();
        var registration = BridgeTestSupport.CreateRegistration(native);
        var clientType = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.ControlPipeClient");
        var client = Activator.CreateInstance(
            clientType,
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
            binder: null,
            args: null,
            culture: null
        );
        Assert.NotNull(client);

        using var server = new NamedPipeServerStream(
            "rhino-mcp-control-v1",
            PipeDirection.InOut,
            1,
            PipeTransmissionMode.Byte,
            PipeOptions.CurrentUserOnly | PipeOptions.Asynchronous
        );
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        var connectTask = BridgeTestSupport.InvokeTask<object>(
            client!,
            "ConnectAsync",
            registration,
            BridgeTestSupport.ContractDigest,
            timeout.Token
        );
        await server.WaitForConnectionAsync(timeout.Token);

        using var registrationFrame = await BridgeTestSupport.ReadFrameAsync(server);
        Assert.NotNull(registrationFrame);
        var registrationJson = registrationFrame!.RootElement;
        Assert.Equal(
            new[]
            {
                "bridgeContractVersion",
                "bridgeVersion",
                "capabilityDigest",
                "contractDigest",
                "kind",
                "pluginId",
                "providerBuildId",
                "protocol",
                "rhinoMajor",
                "schemaVersion",
            }.Order(StringComparer.Ordinal),
            registrationJson.EnumerateObject().Select(property => property.Name).Order(StringComparer.Ordinal)
        );
        Assert.Equal("register", registrationJson.GetProperty("kind").GetString());
        Assert.Equal("rhino-mcp-control-v1", registrationJson.GetProperty("protocol").GetString());
        Assert.Equal("74a1e8f4-1134-4686-a3e5-ee8bf393dec9", registrationJson.GetProperty("pluginId").GetString());
        Assert.Equal(8, registrationJson.GetProperty("rhinoMajor").GetInt32());
        Assert.Equal(
            BridgeTestSupport.ProviderBuildId,
            registrationJson.GetProperty("providerBuildId").GetString()
        );
        Assert.Equal(
            BridgeTestSupport.ContractDigest,
            registrationJson.GetProperty("contractDigest").GetString()
        );
        Assert.False(registrationJson.TryGetProperty("pid", out _));
        Assert.False(registrationJson.TryGetProperty("processId", out _));
        Assert.False(registrationJson.TryGetProperty("path", out _));

        await BridgeTestSupport.WriteFrameAsync(
            server,
            """
            {"schemaVersion":1,"kind":"lease","generation":9,"token":"test-bridge-token-0123456789abcdef","providerBuildId":"sha256:0000000000000000000000000000000000000000000000000000000000000000","contractDigest":"sha256:2222222222222222222222222222222222222222222222222222222222222222"}
            """
        );

        var session = await connectTask.WaitAsync(TimeSpan.FromSeconds(5), TestContext.Current.CancellationToken);
        var lease = BridgeTestSupport.Property(session, "Lease");
        Assert.NotNull(lease);
        Assert.Equal(9UL, BridgeTestSupport.Property(lease!, "Generation"));
        Assert.Equal(BridgeTestSupport.Token, BridgeTestSupport.Property(lease!, "Token"));
        Assert.Equal(BridgeTestSupport.ProviderBuildId, BridgeTestSupport.Property(lease!, "ProviderBuildId"));
        Assert.Equal(BridgeTestSupport.ContractDigest, BridgeTestSupport.Property(lease!, "ContractDigest"));

        ((IDisposable)session).Dispose();
    }

    [Theory]
    [InlineData("quiescent")]
    [InlineData("unknown")]
    public async Task Ready_frame_v2_contains_exact_prior_session_state(string previousState)
    {
        using var native = NativeTestAssembly.Load();
        using var stream = new MemoryStream();
        using var session = CreateControlSession(native, stream);
        await ReportReady(session, previousState);
        stream.Position = 0;
        using var frame = await BridgeTestSupport.ReadFrameAsync(stream);
        Assert.NotNull(frame);
        var root = frame!.RootElement;
        Assert.Equal(2, root.GetProperty("schemaVersion").GetInt32());
        Assert.Equal("bridgeReady", root.GetProperty("kind").GetString());
        Assert.Equal(1UL, root.GetProperty("generation").GetUInt64());
        Assert.Equal(1234, root.GetProperty("port").GetInt32());
        Assert.Equal(BridgeTestSupport.ContractDigest, root.GetProperty("capabilityDigest").GetString());
        Assert.Equal(previousState, root.GetProperty("previousSessionState").GetString());
        Assert.Equal(new[] { "capabilityDigest", "generation", "kind", "port", "previousSessionState", "schemaVersion" },
            root.EnumerateObject().Select(property => property.Name).Order(StringComparer.Ordinal));
    }

    [Theory]
    [InlineData("")]
    [InlineData("idle")]
    [InlineData("quiescent ")]
    [InlineData("Unknown")]
    public async Task Ready_frame_rejects_invalid_prior_session_state_before_writing(string previousState)
    {
        using var native = NativeTestAssembly.Load();
        using var stream = new MemoryStream();
        using var session = CreateControlSession(native, stream);
        await Assert.ThrowsAsync<InvalidDataException>(() => ReportReady(session, previousState));
        Assert.Equal(0, stream.Length);
    }

    private static IDisposable CreateControlSession(NativeTestAssembly native, Stream stream) =>
        (IDisposable)Activator.CreateInstance(BridgeTestSupport.RequireType(native, "RhinoMcpBridge.ControlPipeSession"),
            BindingFlags.Instance | BindingFlags.NonPublic, null,
            [stream, BridgeTestSupport.CreateLease(native)], null)!;

    private static Task ReportReady(object session, string previousState)
    {
        var method = session.GetType().GetMethod("ReportReadyAsync")!;
        object[] arguments = [1234, BridgeTestSupport.ContractDigest, previousState, TestContext.Current.CancellationToken];
        return (Task)method.Invoke(session, arguments)!;
    }

    [Fact]
    public void Registration_must_match_the_signed_protocol_contract_digest()
    {
        using var native = NativeTestAssembly.Load();
        var registration = BridgeTestSupport.CreateRegistration(
            native,
            "sha256:1111111111111111111111111111111111111111111111111111111111111111"
        );
        var clientType = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.ControlPipeClient");
        var validate = clientType.GetMethod(
            "ValidateRegistration",
            BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic
        );
        Assert.NotNull(validate);

        var error = Assert.Throws<TargetInvocationException>(() =>
            validate!.Invoke(null, [registration, BridgeTestSupport.ContractDigest])
        );
        Assert.IsType<InvalidDataException>(error.InnerException);
    }

    [Fact]
    public void Registration_must_match_the_provider_build_identity_embedded_in_the_bridge()
    {
        using var native = NativeTestAssembly.Load();
        var registration = BridgeTestSupport.CreateRegistration(native);
        var registrationType = registration.GetType();
        var buildId = registrationType.GetProperty("ProviderBuildId");
        Assert.NotNull(buildId);
        buildId!.SetValue(
            registration,
            "sha256:1111111111111111111111111111111111111111111111111111111111111111"
        );

        var clientType = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.ControlPipeClient");
        var validate = clientType.GetMethod(
            "ValidateRegistration",
            BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic
        );
        Assert.NotNull(validate);

        var error = Assert.Throws<TargetInvocationException>(() =>
            validate!.Invoke(null, [registration, BridgeTestSupport.ContractDigest])
        );
        Assert.IsType<InvalidDataException>(error.InnerException);
    }

    [Theory]
    [InlineData(0, 250)]
    [InlineData(1, 500)]
    [InlineData(2, 1000)]
    [InlineData(3, 2000)]
    [InlineData(4, 4000)]
    [InlineData(5, 5000)]
    [InlineData(20, 5000)]
    public void Retry_delay_is_exponential_and_has_a_fixed_ceiling(int attempt, int expectedMilliseconds)
    {
        using var native = NativeTestAssembly.Load();
        var clientType = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.ControlPipeClient");
        var retryDelay = clientType.GetMethod(
            "RetryDelay",
            BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic
        );
        Assert.NotNull(retryDelay);

        var delay = (TimeSpan)retryDelay!.Invoke(null, [attempt])!;

        Assert.Equal(TimeSpan.FromMilliseconds(expectedMilliseconds), delay);
    }

    [Fact]
    public async Task Waiting_for_desktop_is_cancellable_and_does_not_become_an_error_state()
    {
        using var native = NativeTestAssembly.Load();
        var registration = BridgeTestSupport.CreateRegistration(native);
        var clientType = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.ControlPipeClient");
        var client = Activator.CreateInstance(
            clientType,
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
            binder: null,
            args: null,
            culture: null
        );
        Assert.NotNull(client);
        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(400));
        var method = clientType.GetMethod(
            "ConnectWithRetryAsync",
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
        );
        Assert.NotNull(method);
        var task = (Task?)method!.Invoke(
            client,
            [registration, BridgeTestSupport.ContractDigest, cancellation.Token]
        );
        Assert.NotNull(task);

        await Assert.ThrowsAnyAsync<OperationCanceledException>(async () =>
            await task!.WaitAsync(TimeSpan.FromSeconds(3), TestContext.Current.CancellationToken)
        );
        Assert.DoesNotContain(
            clientType.GetProperties(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic),
            property => property.Name.Contains("Error", StringComparison.OrdinalIgnoreCase)
        );
    }
}
