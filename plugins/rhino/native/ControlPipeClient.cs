using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace RhinoMcpBridge;

internal sealed record BridgeRegistration(
    uint SchemaVersion,
    string Kind,
    string Protocol,
    string PluginId,
    string BridgeVersion,
    uint BridgeContractVersion,
    uint RhinoMajor,
    string ProviderBuildId,
    string ContractDigest,
    string CapabilityDigest
);

internal sealed record BridgeLease(
    ulong Generation,
    string Token,
    string ProviderBuildId,
    string ContractDigest
);

internal sealed class ControlPipeSession : IDisposable
{
    private Stream? _pipe;
    private int _disposed;

    internal ControlPipeSession(Stream pipe, BridgeLease lease)
    {
        _pipe = pipe ?? throw new ArgumentNullException(nameof(pipe));
        Lease = lease ?? throw new ArgumentNullException(nameof(lease));
    }

    public BridgeLease Lease { get; }

    public async Task ReportReadyAsync(
        int port,
        string capabilityDigest,
        CancellationToken cancellationToken
    )
    {
        if (port is < 1 or > 65535)
        {
            throw new ArgumentOutOfRangeException(nameof(port));
        }

        if (!BridgeWire.IsSha256(capabilityDigest))
        {
            throw new InvalidDataException("Bridge capability identity is invalid.");
        }

        var pipe = GetPipe();
        await BridgeWire.WriteJsonFrameAsync(
            pipe,
            new
            {
                schemaVersion = 1,
                kind = "bridgeReady",
                generation = Lease.Generation,
                port,
                capabilityDigest,
            },
            BridgeWire.MaxControlFrameSize,
            cancellationToken
        ).ConfigureAwait(false);
    }

    public async Task WaitForHostDisconnectAsync(CancellationToken cancellationToken)
    {
        var pipe = GetPipe();
        var payload = await BridgeWire.ReadFramePayloadAsync(
            pipe,
            BridgeWire.MaxControlFrameSize,
            cancellationToken,
            allowCleanEof: true
        ).ConfigureAwait(false);

        if (payload is not null)
        {
            throw new InvalidDataException("Unexpected control message.");
        }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
        {
            return;
        }

        Interlocked.Exchange(ref _pipe, null)?.Dispose();
    }

    private Stream GetPipe() =>
        Volatile.Read(ref _pipe)
        ?? throw new ObjectDisposedException(nameof(ControlPipeSession));
}

internal sealed class ControlPipeClient
{
    public const string PipeName = "rhino-mcp-control-v1";
    public const string ServerName = ".";
#if NET48
    // .NET Framework 4.8's reference assembly has no CurrentUserOnly value. The peer SID and
    // elevation are checked from the connected pipe handle immediately after connect instead.
    public const PipeOptions PipeOptions = System.IO.Pipes.PipeOptions.Asynchronous;
#else
    public const PipeOptions PipeOptions =
        System.IO.Pipes.PipeOptions.CurrentUserOnly | System.IO.Pipes.PipeOptions.Asynchronous;
#endif

    private const int ConnectTimeoutMilliseconds = 1500;

    public async Task<ControlPipeSession> ConnectAsync(
        BridgeRegistration registration,
        string expectedContractDigest,
        CancellationToken cancellationToken
    )
    {
        if (!BridgeWire.IsSha256(expectedContractDigest))
        {
            throw new InvalidDataException("Expected Bridge contract identity is invalid.");
        }
        ValidateRegistration(registration, expectedContractDigest);

        var pipe = new NamedPipeClientStream(
            ServerName,
            PipeName,
            PipeDirection.InOut,
            PipeOptions,
            TokenImpersonationLevel.None
        );

        try
        {
            await pipe.ConnectAsync(ConnectTimeoutMilliseconds, cancellationToken).ConfigureAwait(false);
#if NET48
            PipePeerIdentity.EnsureCurrentUserServer(pipe);
#endif
            await BridgeWire.WriteJsonFrameAsync(
                pipe,
                registration,
                BridgeWire.MaxControlFrameSize,
                cancellationToken
            ).ConfigureAwait(false);

            var payload = await BridgeWire.ReadFramePayloadAsync(
                pipe,
                BridgeWire.MaxControlFrameSize,
                cancellationToken,
                allowCleanEof: false
            ).ConfigureAwait(false)
                ?? throw new InvalidDataException("Control host closed before issuing a Bridge lease.");
            var lease = ParseLease(payload, expectedContractDigest);
            return new ControlPipeSession(pipe, lease);
        }
        catch
        {
            pipe.Dispose();
            throw;
        }
    }

    public async Task<ControlPipeSession> ConnectWithRetryAsync(
        BridgeRegistration registration,
        string expectedContractDigest,
        CancellationToken cancellationToken
    )
    {
        var attempt = 0;
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                return await ConnectAsync(registration, expectedContractDigest, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception exception) when (IsRetryable(exception))
            {
                await Task.Delay(RetryDelay(attempt), cancellationToken).ConfigureAwait(false);
                attempt = Math.Min(attempt + 1, 6);
            }
        }
    }

    internal static TimeSpan RetryDelay(int attempt)
    {
        if (attempt < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(attempt));
        }

        var delayMilliseconds = attempt switch
        {
            0 => 250,
            1 => 500,
            2 => 1000,
            3 => 2000,
            4 => 4000,
            _ => 5000,
        };
        return TimeSpan.FromMilliseconds(delayMilliseconds);
    }

    internal static BridgeLease ParseLease(byte[] payload, string expectedContractDigest)
    {
        using var document = BridgeWire.ParseStrictJson(payload);
        var root = document.RootElement;
        BridgeWire.RequireExactProperties(
            root,
            "schemaVersion",
            "kind",
            "generation",
            "token",
            "providerBuildId",
            "contractDigest"
        );

        if (
            !root.GetProperty("schemaVersion").TryGetUInt32(out var schemaVersion)
            || schemaVersion != 1
            || root.GetProperty("kind").GetString() != "lease"
            || !root.GetProperty("generation").TryGetUInt64(out var generation)
            || generation == 0
        )
        {
            throw new InvalidDataException("Control lease identity is invalid.");
        }

        var token = root.GetProperty("token").GetString();
        var providerBuildId = root.GetProperty("providerBuildId").GetString();
        var contractDigest = root.GetProperty("contractDigest").GetString();
        if (
            !BridgeWire.IsOpaqueToken(token)
            || !BridgeWire.IsSha256(providerBuildId)
            || !BridgeWire.IsSha256(contractDigest)
            || !string.Equals(contractDigest, expectedContractDigest, StringComparison.Ordinal)
        )
        {
            throw new InvalidDataException("Control lease identity is invalid.");
        }

        return new BridgeLease(generation, token!, providerBuildId!, contractDigest!);
    }

    private static void ValidateRegistration(
        BridgeRegistration registration,
        string expectedContractDigest
    )
    {
        if (
            registration is null
            || registration.SchemaVersion != 1
            || registration.Kind != "register"
            || registration.Protocol != PipeName
            || registration.PluginId != "74a1e8f4-1134-4686-a3e5-ee8bf393dec9"
            || registration.BridgeContractVersion != 1
            || registration.RhinoMajor != 8
            || !BridgeWire.IsSha256(registration.ProviderBuildId)
            || !string.Equals(
                registration.ProviderBuildId,
                BridgeIdentity.ReadProviderBuildId(),
                StringComparison.Ordinal
            )
            || !BridgeWire.IsSha256(registration.ContractDigest)
            || !string.Equals(
                registration.ContractDigest,
                expectedContractDigest,
                StringComparison.Ordinal
            )
            || !BridgeWire.IsSha256(registration.CapabilityDigest)
            || !IsSemVer(registration.BridgeVersion)
        )
        {
            throw new InvalidDataException("Bridge registration identity is invalid.");
        }
    }

    private static bool IsSemVer(string? value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return false;
        }

        var parts = value!.Split('.');
        return parts.Length == 3
            && parts.All(part => part.Length > 0 && part.All(char.IsDigit));
    }

    private static bool IsRetryable(Exception exception) =>
        exception is IOException
            or TimeoutException
            or UnauthorizedAccessException
            or InvalidDataException
            or JsonException;
}

internal static class PipePeerIdentity
{
#if NET48
    private const uint ProcessQueryLimitedInformation = 0x1000;
    private const uint TokenQuery = 0x0008;
    private const int TokenUserInformation = 1;
    private const int TokenElevationInformation = 20;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetNamedPipeServerProcessId(IntPtr pipeHandle, out uint serverProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint desiredAccess, bool inheritHandle, uint processId);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool OpenProcessToken(IntPtr processHandle, uint desiredAccess, out IntPtr tokenHandle);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetTokenInformation(
        IntPtr tokenHandle,
        int tokenInformationClass,
        IntPtr tokenInformation,
        uint tokenInformationLength,
        out uint returnLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    internal static void EnsureCurrentUserServer(NamedPipeClientStream pipe)
    {
        if (!GetNamedPipeServerProcessId(pipe.SafePipeHandle.DangerousGetHandle(), out var processId))
        {
            throw new InvalidDataException("Control pipe server identity could not be verified.");
        }

        var processHandle = OpenProcess(ProcessQueryLimitedInformation, inheritHandle: false, processId);
        if (processHandle == IntPtr.Zero)
        {
            throw new InvalidDataException("Control pipe server identity could not be verified.");
        }

        IntPtr serverToken = IntPtr.Zero;
        try
        {
            if (!OpenProcessToken(processHandle, TokenQuery, out serverToken))
            {
                throw new InvalidDataException("Control pipe server identity could not be verified.");
            }

            using var currentIdentity = WindowsIdentity.GetCurrent();
            var currentUser = currentIdentity.User;
            if (
                currentUser is null
                || !currentUser.Equals(ReadUserSid(serverToken))
                || ReadElevation(serverToken) != ReadElevation(currentIdentity.Token)
            )
            {
                throw new InvalidDataException("Control pipe server identity could not be verified.");
            }
        }
        finally
        {
            if (serverToken != IntPtr.Zero)
            {
                CloseHandle(serverToken);
            }

            CloseHandle(processHandle);
        }
    }

    private static SecurityIdentifier ReadUserSid(IntPtr tokenHandle)
    {
        _ = GetTokenInformation(
            tokenHandle,
            TokenUserInformation,
            IntPtr.Zero,
            0,
            out var requiredLength
        );
        if (requiredLength == 0)
        {
            throw new InvalidDataException("Control pipe server identity could not be verified.");
        }

        var buffer = Marshal.AllocHGlobal(checked((int)requiredLength));
        try
        {
            if (!GetTokenInformation(
                    tokenHandle,
                    TokenUserInformation,
                    buffer,
                    requiredLength,
                    out _
                ))
            {
                throw new InvalidDataException("Control pipe server identity could not be verified.");
            }

            return new SecurityIdentifier(Marshal.ReadIntPtr(buffer));
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    private static bool ReadElevation(IntPtr tokenHandle)
    {
        var buffer = Marshal.AllocHGlobal(sizeof(int));
        try
        {
            if (!GetTokenInformation(
                    tokenHandle,
                    TokenElevationInformation,
                    buffer,
                    sizeof(int),
                    out _
                ))
            {
                throw new InvalidDataException("Control pipe server identity could not be verified.");
            }

            return Marshal.ReadInt32(buffer) != 0;
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }
#endif
}

internal static class BridgeWire
{
    internal const int MaxControlFrameSize = 65_536;
    internal const int MaxBridgeFrameSize = 67_108_864;

    private static readonly UTF8Encoding StrictUtf8 = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = false,
        MaxDepth = 64,
    };

    internal static async Task<byte[]?> ReadFramePayloadAsync(
        Stream stream,
        int maximumFrameSize,
        CancellationToken cancellationToken,
        bool allowCleanEof
    )
    {
        var header = new byte[4];
        var firstByte = await stream.ReadAsync(header, 0, 1, cancellationToken).ConfigureAwait(false);
        if (firstByte == 0)
        {
            if (allowCleanEof)
            {
                return null;
            }

            throw new EndOfStreamException("Peer closed before a frame arrived.");
        }

        await ReadExactlyAsync(stream, header, 1, 3, cancellationToken).ConfigureAwait(false);
        var size = ((uint)header[0] << 24)
            | ((uint)header[1] << 16)
            | ((uint)header[2] << 8)
            | header[3];
        if (size == 0 || size > maximumFrameSize)
        {
            throw new InvalidDataException("Frame size is outside the allowed range.");
        }

        var payload = new byte[(int)size];
        await ReadExactlyAsync(stream, payload, 0, payload.Length, cancellationToken).ConfigureAwait(false);
        return payload;
    }

    internal static async Task<JsonDocument?> ReadJsonFrameAsync(
        Stream stream,
        int maximumFrameSize,
        CancellationToken cancellationToken,
        bool allowCleanEof
    )
    {
        var payload = await ReadFramePayloadAsync(
            stream,
            maximumFrameSize,
            cancellationToken,
            allowCleanEof
        ).ConfigureAwait(false);
        return payload is null ? null : ParseStrictJson(payload);
    }

    internal static async Task WriteJsonFrameAsync(
        Stream stream,
        object value,
        int maximumFrameSize,
        CancellationToken cancellationToken
    )
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(value, JsonOptions);
        await WriteFramePayloadAsync(stream, payload, maximumFrameSize, cancellationToken).ConfigureAwait(false);
    }

    internal static async Task WriteFramePayloadAsync(
        Stream stream,
        byte[] payload,
        int maximumFrameSize,
        CancellationToken cancellationToken
    )
    {
        if (payload.Length == 0 || payload.Length > maximumFrameSize)
        {
            throw new InvalidDataException("Frame size is outside the allowed range.");
        }

        var header = new byte[]
        {
            (byte)(payload.Length >> 24),
            (byte)(payload.Length >> 16),
            (byte)(payload.Length >> 8),
            (byte)payload.Length,
        };
        await stream.WriteAsync(header, 0, header.Length, cancellationToken).ConfigureAwait(false);
        await stream.WriteAsync(payload, 0, payload.Length, cancellationToken).ConfigureAwait(false);
        await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    internal static JsonDocument ParseStrictJson(byte[] payload)
    {
        var text = StrictUtf8.GetString(payload);
        var document = JsonDocument.Parse(
            text,
            new JsonDocumentOptions { AllowTrailingCommas = false, CommentHandling = JsonCommentHandling.Disallow, MaxDepth = 64 }
        );
        try
        {
            RejectDuplicateProperties(document.RootElement);
            return document;
        }
        catch
        {
            document.Dispose();
            throw;
        }
    }

    internal static byte[] Serialize(object value) => JsonSerializer.SerializeToUtf8Bytes(value, JsonOptions);

    internal static void RequireExactProperties(JsonElement value, params string[] expectedProperties)
    {
        if (value.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidDataException("JSON envelope must be an object.");
        }

        var actual = value.EnumerateObject().Select(property => property.Name).OrderBy(name => name, StringComparer.Ordinal);
        var expected = expectedProperties.OrderBy(name => name, StringComparer.Ordinal);
        if (!actual.SequenceEqual(expected, StringComparer.Ordinal))
        {
            throw new InvalidDataException("JSON envelope fields do not match the protocol.");
        }
    }

    internal static bool IsSha256(string? value)
    {
        if (value is null || value.Length != 71 || !value.StartsWith("sha256:", StringComparison.Ordinal))
        {
            return false;
        }

        return value.AsSpan(7).ToArray().All(character =>
            character is >= '0' and <= '9' or >= 'a' and <= 'f'
        );
    }

    internal static bool IsOpaqueToken(string? value)
    {
        return value is { Length: >= 32 and <= 512 }
            && value.All(character =>
                character is >= 'a' and <= 'z'
                    or >= 'A' and <= 'Z'
                    or >= '0' and <= '9'
                    or '-' or '_'
            );
    }

    private static async Task ReadExactlyAsync(
        Stream stream,
        byte[] buffer,
        int offset,
        int count,
        CancellationToken cancellationToken
    )
    {
        var total = 0;
        while (total < count)
        {
            var read = await stream.ReadAsync(buffer, offset + total, count - total, cancellationToken)
                .ConfigureAwait(false);
            if (read == 0)
            {
                throw new EndOfStreamException("Peer closed in the middle of a frame.");
            }

            total += read;
        }
    }

    private static void RejectDuplicateProperties(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Object)
        {
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (var property in value.EnumerateObject())
            {
                if (!names.Add(property.Name))
                {
                    throw new InvalidDataException("JSON contains a duplicate property.");
                }

                RejectDuplicateProperties(property.Value);
            }
        }
        else if (value.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in value.EnumerateArray())
            {
                RejectDuplicateProperties(item);
            }
        }
    }
}
