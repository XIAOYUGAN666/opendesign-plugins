using System.Net;
using System.Net.Sockets;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace RhinoMcpBridge;

internal sealed class BridgeServer : IDisposable
{
    private static readonly HashSet<string> ProbeFields = new(StringComparer.Ordinal) { "type", "nonce" };
    private static readonly HashSet<string> AuthenticationFields = new(StringComparer.Ordinal)
    {
        "type",
        "generation",
        "token",
        "connectorBuildId",
        "contractDigest",
    };

    private static readonly HashSet<string> CommandFields = new(StringComparer.Ordinal)
    {
        "type",
        "params",
        "include_delta",
        "include_health",
    };

    private readonly object _gate = new();
    private readonly Func<string, string, bool, bool, CancellationToken, Task<byte[]>> _executor;
    private readonly CancellationTokenSource _shutdown = new();
    private BridgeLease? _lease;
    private TcpListener? _listener;
    private TcpClient? _activeClient;
    private Task? _acceptTask;
    private int _started;
    private int _disposed;

    public BridgeServer(
        BridgeLease lease,
        Func<string, string, bool, bool, CancellationToken, Task<byte[]>> executor
    )
    {
        _lease = lease ?? throw new ArgumentNullException(nameof(lease));
        _executor = executor ?? throw new ArgumentNullException(nameof(executor));
        if (
            lease.Generation == 0
            || !BridgeWire.IsOpaqueToken(lease.Token)
            || !BridgeWire.IsSha256(lease.ConnectorBuildId)
            || !BridgeWire.IsSha256(lease.ContractDigest)
        )
        {
            throw new ArgumentException("Bridge lease identity is invalid.", nameof(lease));
        }
    }

    public int Port { get; private set; }

    public void Start()
    {
        if (Interlocked.CompareExchange(ref _started, 1, 0) != 0)
        {
            throw new InvalidOperationException("Bridge listener has already been started.");
        }

        if (Volatile.Read(ref _disposed) != 0)
        {
            throw new ObjectDisposedException(nameof(BridgeServer));
        }

        var listener = new TcpListener(IPAddress.Loopback, 0);
        try
        {
            listener.Start(2);
            _listener = listener;
            Port = ((IPEndPoint)listener.LocalEndpoint).Port;
            _acceptTask = AcceptLoopAsync(listener, _shutdown.Token);
        }
        catch
        {
            listener.Stop();
            throw;
        }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
        {
            return;
        }

        Interlocked.Exchange(ref _lease, null);
        _shutdown.Cancel();
        try
        {
            Interlocked.Exchange(ref _listener, null)?.Stop();
        }
        catch (SocketException)
        {
            // The listener is already closed.
        }

        TcpClient? client;
        lock (_gate)
        {
            client = _activeClient;
            _activeClient = null;
        }

        try
        {
            client?.Close();
        }
        catch (SocketException)
        {
            // Closing an already disconnected Connector socket is safe.
        }

        _shutdown.Dispose();
    }

    private async Task AcceptLoopAsync(TcpListener listener, CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            TcpClient client;
            try
            {
                client = await listener.AcceptTcpClientAsync().ConfigureAwait(false);
            }
            catch (ObjectDisposedException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (SocketException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (InvalidOperationException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }

            client.NoDelay = true;
            lock (_gate)
            {
                if (cancellationToken.IsCancellationRequested || Volatile.Read(ref _disposed) != 0)
                {
                    client.Close();
                    return;
                }

                if (_activeClient is not null)
                {
                    _ = RejectConcurrentClientAsync(client);
                    continue;
                }

                _activeClient = client;
            }

            _ = Task.Run(() => HandleConnectorAsync(client, cancellationToken));
        }
    }

    private async Task HandleConnectorAsync(TcpClient client, CancellationToken cancellationToken)
    {
        try
        {
            using (client)
            using (var stream = client.GetStream())
            {
                var firstPayload = await BridgeWire.ReadFramePayloadAsync(
                    stream,
                    BridgeWire.MaxBridgeFrameSize,
                    cancellationToken,
                    allowCleanEof: true
                ).ConfigureAwait(false);
                if (firstPayload is null || !TryAuthenticate(firstPayload, out var lease))
                {
                    await WriteAuthenticationRejectedAsync(stream, cancellationToken).ConfigureAwait(false);
                    return;
                }

                await BridgeWire.WriteJsonFrameAsync(
                    stream,
                    new
                    {
                        type = "authenticated",
                        generation = lease.Generation,
                        connectorBuildId = lease.ConnectorBuildId,
                        contractDigest = lease.ContractDigest,
                    },
                    BridgeWire.MaxBridgeFrameSize,
                    cancellationToken
                ).ConfigureAwait(false);

                while (!cancellationToken.IsCancellationRequested)
                {
                    var payload = await BridgeWire.ReadFramePayloadAsync(
                        stream,
                        BridgeWire.MaxBridgeFrameSize,
                        cancellationToken,
                        allowCleanEof: true
                    ).ConfigureAwait(false);
                    if (payload is null)
                    {
                        return;
                    }

                    using var document = BridgeWire.ParseStrictJson(payload);
                    var root = document.RootElement;
                    if (TryParseConnectionProbe(root, out var nonce))
                    {
                        await BridgeWire.WriteJsonFrameAsync(
                            stream,
                            new { type = "connectionProbeAck", nonce },
                            BridgeWire.MaxBridgeFrameSize,
                            cancellationToken
                        ).ConfigureAwait(false);
                        continue;
                    }

                    if (!TryParseCommand(root, out var commandType, out var parametersJson, out var includeDelta, out var includeHealth))
                    {
                        await BridgeWire.WriteJsonFrameAsync(
                            stream,
                            new { status = "error", message = "Invalid command envelope." },
                            BridgeWire.MaxBridgeFrameSize,
                            cancellationToken
                        ).ConfigureAwait(false);
                        continue;
                    }

                    byte[] response;
                    try
                    {
                        var result = await _executor(
                            commandType,
                            parametersJson,
                            includeDelta,
                            includeHealth,
                            cancellationToken
                        ).ConfigureAwait(false);
                        using var resultDocument = BridgeWire.ParseStrictJson(result);
                        response = BridgeWire.Serialize(
                            new { status = "success", result = resultDocument.RootElement }
                        );
                    }
                    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                    {
                        return;
                    }
                    catch (NativeCommandException exception)
                    {
                        response = BridgeWire.Serialize(
                            new { status = "error", message = exception.SafeMessage }
                        );
                    }
                    catch (Exception)
                    {
                        response = BridgeWire.Serialize(
                            new { status = "error", message = "Rhino could not complete the command." }
                        );
                    }

                    await BridgeWire.WriteFramePayloadAsync(
                        stream,
                        response,
                        BridgeWire.MaxBridgeFrameSize,
                        cancellationToken
                    ).ConfigureAwait(false);
                }
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            // Lease revocation or plug-in shutdown closes the in-flight socket.
        }
        catch (Exception)
        {
            // A malformed or disconnected peer is isolated to this connection.
        }
        finally
        {
            lock (_gate)
            {
                if (ReferenceEquals(_activeClient, client))
                {
                    _activeClient = null;
                }
            }
        }
    }

    private bool TryAuthenticate(byte[] payload, out BridgeLease lease)
    {
        lease = null!;
        BridgeLease? currentLease = Volatile.Read(ref _lease);
        if (currentLease is null)
        {
            return false;
        }

        using var document = BridgeWire.ParseStrictJson(payload);
        var root = document.RootElement;
        if (!HasExactFields(root, AuthenticationFields))
        {
            return false;
        }

        if (
            root.GetProperty("type").GetString() != "authenticate"
            || !root.GetProperty("generation").TryGetUInt64(out var generation)
            || generation != currentLease.Generation
        )
        {
            return false;
        }

        var token = root.GetProperty("token").GetString();
        var connectorBuildId = root.GetProperty("connectorBuildId").GetString();
        var contractDigest = root.GetProperty("contractDigest").GetString();
        if (
            !FixedTimeEquals(token, currentLease.Token)
            || !string.Equals(connectorBuildId, currentLease.ConnectorBuildId, StringComparison.Ordinal)
            || !string.Equals(contractDigest, currentLease.ContractDigest, StringComparison.Ordinal)
        )
        {
            return false;
        }

        lease = currentLease;
        return true;
    }

    private static bool TryParseConnectionProbe(JsonElement root, out string nonce)
    {
        nonce = string.Empty;
        if (!HasExactFields(root, ProbeFields)
            || root.GetProperty("type").ValueKind != JsonValueKind.String
            || root.GetProperty("type").GetString() != "connectionProbe"
            || root.GetProperty("nonce").ValueKind != JsonValueKind.String)
        {
            return false;
        }
        var value = root.GetProperty("nonce").GetString();
        if (value is null || value.Length != 32 || value.Any(c => !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))))
        {
            return false;
        }
        nonce = value;
        return true;
    }

    private static bool TryParseCommand(
        JsonElement root,
        out string commandType,
        out string parametersJson,
        out bool includeDelta,
        out bool includeHealth
    )
    {
        commandType = string.Empty;
        parametersJson = string.Empty;
        includeDelta = false;
        includeHealth = false;

        if (root.ValueKind != JsonValueKind.Object)
        {
            return false;
        }

        var propertyNames = root.EnumerateObject().Select(property => property.Name).ToArray();
        if (
            propertyNames.Distinct(StringComparer.Ordinal).Count() != propertyNames.Length
            || propertyNames.Any(name => !CommandFields.Contains(name))
            || !propertyNames.Contains("type", StringComparer.Ordinal)
            || !propertyNames.Contains("params", StringComparer.Ordinal)
        )
        {
            return false;
        }

        var typeElement = root.GetProperty("type");
        var parametersElement = root.GetProperty("params");
        if (typeElement.ValueKind != JsonValueKind.String || parametersElement.ValueKind != JsonValueKind.Object)
        {
            return false;
        }

        foreach (var flagName in new[] { "include_delta", "include_health" })
        {
            if (root.TryGetProperty(flagName, out var flag) && flag.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            {
                return false;
            }
        }

        commandType = typeElement.GetString()!;
        parametersJson = parametersElement.GetRawText();
        includeDelta = root.TryGetProperty("include_delta", out var includeDeltaElement)
            && includeDeltaElement.GetBoolean();
        includeHealth = root.TryGetProperty("include_health", out var includeHealthElement)
            && includeHealthElement.GetBoolean();
        return !string.IsNullOrWhiteSpace(commandType);
    }

    private static bool HasExactFields(JsonElement value, HashSet<string> expected)
    {
        if (value.ValueKind != JsonValueKind.Object)
        {
            return false;
        }

        var names = value.EnumerateObject().Select(property => property.Name).ToArray();
        return names.Length == expected.Count
            && names.Distinct(StringComparer.Ordinal).Count() == names.Length
            && names.All(expected.Contains);
    }

    private static bool FixedTimeEquals(string? left, string? right)
    {
        if (left is null || right is null)
        {
            return false;
        }

        var difference = left.Length ^ right.Length;
        var length = Math.Max(left.Length, right.Length);
        for (var index = 0; index < length; index++)
        {
            var leftCharacter = index < left.Length ? left[index] : '\0';
            var rightCharacter = index < right.Length ? right[index] : '\0';
            difference |= leftCharacter ^ rightCharacter;
        }

        return difference == 0;
    }

    private static async Task RejectConcurrentClientAsync(TcpClient client)
    {
        using (client)
        {
            try
            {
                using var stream = client.GetStream();
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                _ = await BridgeWire.ReadFramePayloadAsync(
                    stream,
                    BridgeWire.MaxBridgeFrameSize,
                    timeout.Token,
                    allowCleanEof: false
                ).ConfigureAwait(false);
                await WriteAuthenticationRejectedAsync(stream, CancellationToken.None).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // Refusing another active Connector never affects the current session.
            }
        }
    }

    private static Task WriteAuthenticationRejectedAsync(Stream stream, CancellationToken cancellationToken) =>
        BridgeWire.WriteJsonFrameAsync(
            stream,
            new { type = "authenticationRejected" },
            BridgeWire.MaxBridgeFrameSize,
            cancellationToken
        );
}
