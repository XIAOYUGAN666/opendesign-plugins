using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;
using Xunit;

namespace RhinoMcpBridge.Tests;

internal sealed class NativeTestAssembly : IDisposable
{
    private const string SourceRootEnvironmentVariable = "RHINO_NATIVE_SOURCE_ROOT";
    private const string TestAssemblyEnvironmentVariable = "RHINO_NATIVE_TEST_ASSEMBLY";
    private readonly AssemblyLoadContext _loadContext;

    private NativeTestAssembly(Assembly assembly, AssemblyLoadContext loadContext)
    {
        Assembly = assembly;
        _loadContext = loadContext;
    }

    public Assembly Assembly { get; }

    public string PluginRoot { get; private init; } = string.Empty;

    public Assembly LoadDependency(string assemblyName) =>
        _loadContext.LoadFromAssemblyName(new AssemblyName(assemblyName));

    public static NativeTestAssembly Load()
    {
        var nativeRoot = FindNativeRoot();
        var pluginRoot = nativeRoot.Parent?.FullName
            ?? throw new DirectoryNotFoundException("Could not locate the plug-in root.");
        var configuredAssemblyPath = Environment.GetEnvironmentVariable(TestAssemblyEnvironmentVariable);
        var assemblyPath = string.IsNullOrWhiteSpace(configuredAssemblyPath)
            ? Path.Combine(nativeRoot.FullName, "bin", "Release", "net8.0", "RhinoMcpBridge.rhp")
            : Path.IsPathRooted(configuredAssemblyPath)
                ? Path.GetFullPath(configuredAssemblyPath)
                : throw new DirectoryNotFoundException(
                    "The isolated Rhino test assembly path must be absolute."
                );
        if (!Path.IsPathRooted(assemblyPath))
        {
            throw new DirectoryNotFoundException("The isolated Rhino test assembly path must be absolute.");
        }
        Assert.True(File.Exists(assemblyPath), $"Missing native target: {assemblyPath}");

        var loadContext = new AssemblyLoadContext(
            $"RhinoMcpBridge-tests-{Guid.NewGuid():N}",
            isCollectible: true
        );
        loadContext.Resolving += (_, name) =>
        {
            var dependencyPath = Path.Combine(AppContext.BaseDirectory, $"{name.Name}.dll");
            return File.Exists(dependencyPath)
                ? loadContext.LoadFromAssemblyPath(dependencyPath)
                : null;
        };

        return new NativeTestAssembly(
            loadContext.LoadFromAssemblyPath(assemblyPath),
            loadContext
        ) { PluginRoot = pluginRoot };
    }

    public HashSet<string> ReadProtocolCommandNames()
    {
        var protocolPath = Path.Combine(PluginRoot, "contracts", "protocol.json");
        using var document = JsonDocument.Parse(File.ReadAllText(protocolPath));
        return document
            .RootElement
            .GetProperty("$defs")
            .GetProperty("command")
            .GetProperty("properties")
            .GetProperty("type")
            .GetProperty("enum")
            .EnumerateArray()
            .Select(value => value.GetString()!)
            .ToHashSet(StringComparer.Ordinal);
    }

    public void Dispose() => _loadContext.Unload();

    private static DirectoryInfo FindNativeRoot()
    {
        var configuredRoot = Environment.GetEnvironmentVariable(SourceRootEnvironmentVariable);
        if (!string.IsNullOrWhiteSpace(configuredRoot))
        {
            if (!Path.IsPathRooted(configuredRoot))
            {
                throw new DirectoryNotFoundException(
                    "The configured Rhino native source root must be absolute."
                );
            }

            var fullPath = Path.GetFullPath(configuredRoot);
            if (!File.Exists(Path.Combine(fullPath, "RhinoMcpBridge.sln")))
            {
                throw new DirectoryNotFoundException(
                    "The configured Rhino native source root is invalid."
                );
            }

            return new DirectoryInfo(fullPath);
        }

        for (
            DirectoryInfo? directory = new(AppContext.BaseDirectory);
            directory is not null;
            directory = directory.Parent
        )
        {
            if (File.Exists(Path.Combine(directory.FullName, "RhinoMcpBridge.sln")))
            {
                return directory;
            }
        }

        throw new DirectoryNotFoundException(
            "Could not locate RhinoMcpBridge.sln above the test output directory."
        );
    }
}
