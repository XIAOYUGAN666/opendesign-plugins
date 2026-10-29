using System.Reflection;
using System.Runtime.Loader;
using System.Text;
using System.Text.Json;

internal static class Program
{
    private const int MaxConfigurationLength = 4096;
    private const string CapabilityResult = """
        {"version":"0.1.2","command_count":3,"commands":[{"name":"create_layer","read_only":false,"supports_dry_run":false},{"name":"delete_layer","read_only":false,"supports_dry_run":false},{"name":"describe_capabilities","read_only":true,"supports_dry_run":false}],"perception":{"description":"Test host","envelope_flags":[]}}
        """;

    public static async Task<int> Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--capability-digest")
        {
            return WriteCapabilityDigest(args[1]);
        }

        if (args.Length != 1 || !File.Exists(args[0]))
        {
            Console.Error.WriteLine("Native Bridge test host configuration is invalid.");
            return 2;
        }

        var configuration = await ReadConfigurationAsync();
        if (configuration is null)
        {
            Console.Error.WriteLine("Native Bridge test host configuration is invalid.");
            return 2;
        }

        var pluginPath = Path.GetFullPath(args[0]);
        var loadContext = new PluginLoadContext(pluginPath);
        object? server = null;
        try
        {
            var assembly = loadContext.LoadFromAssemblyPath(pluginPath);
            var identityType = assembly.GetType("RhinoMcpBridge.BridgeIdentity", throwOnError: true)!;
            var digest = (string)identityType
                .GetMethod("ComputeContractDigest", BindingFlags.Static | BindingFlags.NonPublic)!
                .Invoke(null, null)!;
            var connectorBuildId = (string)identityType
                .GetMethod("ReadConnectorBuildId", BindingFlags.Static | BindingFlags.NonPublic)!
                .Invoke(null, null)!;

            var leaseType = assembly.GetType("RhinoMcpBridge.BridgeLease", throwOnError: true)!;
            var lease = leaseType
                .GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                .Single(constructor => constructor.GetParameters().Length == 4)
                .Invoke([configuration.Generation, configuration.BridgeToken, connectorBuildId, digest]);

            Func<string, string, bool, bool, CancellationToken, Task<byte[]>> executor =
                (command, parameters, _, _, cancellationToken) =>
                    ExecuteCommandAsync(command, parameters, configuration.DelayCommand, cancellationToken);
            var serverType = assembly.GetType("RhinoMcpBridge.BridgeServer", throwOnError: true)!;
            server = Activator.CreateInstance(
                serverType,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                binder: null,
                args: [lease, executor],
                culture: null
            );
            if (server is null)
            {
                throw new InvalidOperationException();
            }

            serverType.GetMethod("Start", BindingFlags.Instance | BindingFlags.Public)!.Invoke(server, null);
            var port = (int)serverType.GetProperty("Port", BindingFlags.Instance | BindingFlags.Public)!.GetValue(server)!;
            Console.Out.WriteLine(JsonSerializer.Serialize(new
            {
                kind = "bridgeReady",
                host = "127.0.0.1",
                port,
                connectorBuildId,
            }));
            Console.Out.Flush();
            await Console.In.ReadLineAsync();
            return 0;
        }
        catch (Exception)
        {
            Console.Error.WriteLine("Native Bridge test host could not start.");
            return 1;
        }
        finally
        {
            (server as IDisposable)?.Dispose();
            loadContext.Unload();
        }
    }

    private static int WriteCapabilityDigest(string pluginPath)
    {
        if (!File.Exists(pluginPath))
        {
            Console.Error.WriteLine("Native capability probe input is invalid.");
            return 2;
        }

        var loadContext = new PluginLoadContext(Path.GetFullPath(pluginPath));
        try
        {
            var assembly = loadContext.LoadFromAssemblyPath(Path.GetFullPath(pluginPath));
            var identityType = assembly.GetType("RhinoMcpBridge.BridgeIdentity", throwOnError: true)!;
            var digest = (string)identityType
                .GetMethod(
                    "ComputeCapabilityDigest",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic,
                    Type.EmptyTypes
                )!
                .Invoke(null, null)!;
            if (!IsSha256(digest))
            {
                throw new InvalidOperationException();
            }

            Console.Out.WriteLine(digest);
            return 0;
        }
        catch (Exception)
        {
            Console.Error.WriteLine("Native capability probe could not inspect the compiled Bridge.");
            return 1;
        }
        finally
        {
            loadContext.Unload();
        }
    }

    private static bool IsSha256(string value) =>
        value is { Length: 71 }
        && value.StartsWith("sha256:", StringComparison.Ordinal)
        && value.AsSpan(7).ToArray().All(character =>
            character is >= '0' and <= '9' or >= 'a' and <= 'f'
        );

    private static async Task<TestConfiguration?> ReadConfigurationAsync()
    {
        var line = await Console.In.ReadLineAsync();
        if (line is null || line.Length is 0 or > MaxConfigurationLength)
        {
            return null;
        }

        try
        {
            using var document = JsonDocument.Parse(line, new JsonDocumentOptions { MaxDepth = 4 });
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                return null;
            }

            var fields = root.EnumerateObject().Select(property => property.Name).ToArray();
            if (fields.Length != 3 || fields.Distinct(StringComparer.Ordinal).Count() != 3
                || !fields.Contains("generation", StringComparer.Ordinal)
                || !fields.Contains("bridgeToken", StringComparer.Ordinal)
                || !fields.Contains("delayCommand", StringComparer.Ordinal))
            {
                return null;
            }

            var generationElement = root.GetProperty("generation");
            var tokenElement = root.GetProperty("bridgeToken");
            var delayElement = root.GetProperty("delayCommand");
            if (!generationElement.TryGetUInt64(out var generation) || generation == 0
                || tokenElement.ValueKind != JsonValueKind.String
                || delayElement.ValueKind != JsonValueKind.String)
            {
                return null;
            }

            var token = tokenElement.GetString();
            var delayCommand = delayElement.GetString()!;
            if (string.IsNullOrWhiteSpace(token) || token.Length is < 32 or > 256
                || delayCommand is not ("" or "delete_layer"))
            {
                return null;
            }

            return new TestConfiguration(generation, token, delayCommand);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static async Task<byte[]> ExecuteCommandAsync(
        string command,
        string parameters,
        string delayCommand,
        CancellationToken cancellationToken
    )
    {
        if (command == delayCommand && delayCommand.Length > 0)
        {
            // Exceed the integration Connector's 5-second timeout after successful startup.
            await Task.Delay(TimeSpan.FromSeconds(6), cancellationToken).ConfigureAwait(false);
        }

        if (command == "describe_capabilities")
        {
            return Encoding.UTF8.GetBytes(CapabilityResult);
        }

        if (command == "create_layer")
        {
            using var document = JsonDocument.Parse(parameters);
            var name = document.RootElement.TryGetProperty("name", out var nameElement)
                ? nameElement.GetString() ?? "E2E Layer"
                : "E2E Layer";
            return JsonSerializer.SerializeToUtf8Bytes(new
            {
                id = "11111111-1111-4111-8111-111111111111",
                name,
                color = new { r = 0, g = 0, b = 0 },
            });
        }

        if (command == "delete_layer")
        {
            return JsonSerializer.SerializeToUtf8Bytes(new { message = "Deleted layer." });
        }

        return Encoding.UTF8.GetBytes("{}");
    }

    private sealed record TestConfiguration(ulong Generation, string BridgeToken, string DelayCommand);

    private sealed class PluginLoadContext(string pluginPath) : AssemblyLoadContext(isCollectible: true)
    {
        private readonly AssemblyDependencyResolver _resolver = new(pluginPath);

        protected override Assembly? Load(AssemblyName assemblyName)
        {
            var path = _resolver.ResolveAssemblyToPath(assemblyName);
            if (path is null)
            {
                var fallback = Path.Combine(AppContext.BaseDirectory, $"{assemblyName.Name}.dll");
                path = File.Exists(fallback) ? fallback : null;
            }

            return path is null ? null : LoadFromAssemblyPath(path);
        }
    }
}
