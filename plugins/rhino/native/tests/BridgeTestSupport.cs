using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace RhinoMcpBridge.Tests;

internal static class BridgeTestSupport
{
    internal const string Token = "test-bridge-token-0123456789abcdef";
    internal const string ProviderBuildId = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
    internal const string ContractDigest = "sha256:2222222222222222222222222222222222222222222222222222222222222222";

    internal static object CreateRegistration(
        NativeTestAssembly native,
        string contractDigest = ContractDigest,
        string providerBuildId = ProviderBuildId
    )
    {
        var type = RequireType(native, "RhinoMcpBridge.BridgeRegistration");
        var constructor = type
            .GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .Single(candidate => candidate.GetParameters().Length == 10);
        return constructor.Invoke(
            [
                (uint)1,
                "register",
                "rhino-mcp-control-v1",
                "74a1e8f4-1134-4686-a3e5-ee8bf393dec9",
                "0.1.2",
                (uint)1,
                (uint)8,
                providerBuildId,
                contractDigest,
                ContractDigest,
            ]
        );
    }

    internal static object CreateLease(
        NativeTestAssembly native,
        ulong generation = 1,
        string token = Token,
        string providerBuildId = ProviderBuildId,
        string contractDigest = ContractDigest
    )
    {
        var type = RequireType(native, "RhinoMcpBridge.BridgeLease");
        var constructor = type
            .GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .Single(candidate => candidate.GetParameters().Length == 4);
        return constructor.Invoke([generation, token, providerBuildId, contractDigest]);
    }

    internal static object CreateBridgeServer(
        NativeTestAssembly native,
        object lease,
        Func<string, string, bool, bool, CancellationToken, Task<byte[]>> executor
    )
    {
        var type = RequireType(native, "RhinoMcpBridge.BridgeServer");
        return Activator.CreateInstance(
                type,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                binder: null,
                args: [lease, executor],
                culture: null
            )
            ?? throw new InvalidOperationException("BridgeServer could not be created.");
    }

    internal static object CreateBridgeController(
        NativeTestAssembly native,
        object registration,
        string contractDigest = ContractDigest
    )
    {
        var type = RequireType(native, "RhinoMcpBridge.BridgeController");
        return Activator.CreateInstance(
                type,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                binder: null,
                args: [registration, contractDigest],
                culture: null
            )
            ?? throw new InvalidOperationException("BridgeController could not be created.");
    }

    internal static Type RequireType(NativeTestAssembly native, string name)
    {
        var type = native.Assembly.GetType(name, throwOnError: false);
        Xunit.Assert.NotNull(type);
        return type!;
    }

    internal static void Invoke(object instance, string methodName, params object?[]? arguments)
    {
        var method = instance.GetType().GetMethod(
            methodName,
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
        );
        Xunit.Assert.NotNull(method);
        method!.Invoke(instance, arguments);
    }

    internal static object? Property(object instance, string propertyName) =>
        instance.GetType()
            .GetProperty(propertyName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            ?.GetValue(instance);

    internal static async Task<T> InvokeTask<T>(object instance, string methodName, params object?[] arguments)
    {
        var method = instance.GetType().GetMethod(
            methodName,
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
        );
        Xunit.Assert.NotNull(method);
        var task = (Task?)method!.Invoke(instance, arguments);
        Xunit.Assert.NotNull(task);
        await task!.WaitAsync(TimeSpan.FromSeconds(5));
        var result = task.GetType().GetProperty("Result")?.GetValue(task);
        Xunit.Assert.IsAssignableFrom<T>(result);
        return (T)result!;
    }

    internal static byte[] EncodeFrame(string json)
    {
        var payload = Encoding.UTF8.GetBytes(json);
        var frame = new byte[payload.Length + 4];
        frame[0] = (byte)(payload.Length >> 24);
        frame[1] = (byte)(payload.Length >> 16);
        frame[2] = (byte)(payload.Length >> 8);
        frame[3] = (byte)payload.Length;
        Buffer.BlockCopy(payload, 0, frame, 4, payload.Length);
        return frame;
    }

    internal static byte[] EncodeFrame(byte[] payload)
    {
        var frame = new byte[payload.Length + 4];
        frame[0] = (byte)(payload.Length >> 24);
        frame[1] = (byte)(payload.Length >> 16);
        frame[2] = (byte)(payload.Length >> 8);
        frame[3] = (byte)payload.Length;
        Buffer.BlockCopy(payload, 0, frame, 4, payload.Length);
        return frame;
    }

    internal static async Task WriteFrameAsync(Stream stream, string json)
    {
        var frame = EncodeFrame(json);
        await stream.WriteAsync(frame, 0, frame.Length);
        await stream.FlushAsync();
    }

    internal static async Task WriteRawFrameAsync(Stream stream, byte[] payload)
    {
        var frame = EncodeFrame(payload);
        await stream.WriteAsync(frame, 0, frame.Length);
        await stream.FlushAsync();
    }

    internal static async Task<JsonDocument?> ReadFrameAsync(Stream stream)
    {
        var header = new byte[4];
        var headerRead = await ReadAtMostAsync(stream, header, 4);
        if (headerRead == 0)
        {
            return null;
        }

        if (headerRead != 4)
        {
            throw new IOException("Peer closed in the middle of a frame header.");
        }

        var size = ((uint)header[0] << 24)
            | ((uint)header[1] << 16)
            | ((uint)header[2] << 8)
            | header[3];
        if (size == 0 || size > 67_108_864)
        {
            throw new InvalidDataException("Test peer returned an invalid frame size.");
        }

        var payload = new byte[checked((int)size)];
        var payloadRead = await ReadAtMostAsync(stream, payload, payload.Length);
        if (payloadRead != payload.Length)
        {
            throw new IOException("Peer closed in the middle of a frame payload.");
        }

        return JsonDocument.Parse(payload);
    }

    internal static async Task<TcpClient> ConnectLoopbackAsync(int port)
    {
        var client = new TcpClient(AddressFamily.InterNetwork);
        await client.ConnectAsync(IPAddress.Loopback, port).WaitAsync(TimeSpan.FromSeconds(3));
        return client;
    }

    internal static string AuthenticationJson(
        ulong generation = 1,
        string token = Token,
        string providerBuildId = ProviderBuildId,
        string contractDigest = ContractDigest
    ) =>
        JsonSerializer.Serialize(
            new
            {
                type = "authenticate",
                generation,
                token,
                providerBuildId,
                contractDigest,
            }
        );

    private static async Task<int> ReadAtMostAsync(Stream stream, byte[] buffer, int count)
    {
        var total = 0;
        while (total < count)
        {
            var read = await stream.ReadAsync(buffer, total, count - total);
            if (read == 0)
            {
                break;
            }

            total += read;
        }

        return total;
    }
}
