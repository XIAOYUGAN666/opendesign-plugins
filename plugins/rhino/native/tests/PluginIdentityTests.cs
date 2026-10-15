using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.Loader;
using System.Runtime.CompilerServices;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class PluginIdentityTests
{
    private const string ExpectedAssemblyName = "RhinoMcpBridge";
    private const string ExpectedVersion = "0.1.2.0";
    private const string ExpectedGuid = "74a1e8f4-1134-4686-a3e5-ee8bf393dec9";
    private const string ExpectedLoadTime = "AtStartup";
    private const string ExpectedCapabilityDigestEnvironmentVariable =
        "RHINO_EXPECTED_CAPABILITY_DIGEST";

    [Fact]
    public void BothRhino8TargetsHaveTheSamePermanentPluginIdentity()
    {
        var identities = TargetAssemblyPaths(
                Environment.GetEnvironmentVariable("RHINO_NATIVE_OUTPUT_ROOT")
            )
            .Select(ReadIdentity)
            .ToArray();

        Assert.Equal(2, identities.Length);
        foreach (var identity in identities)
        {
            Assert.Equal(ExpectedAssemblyName, identity.Name);
            Assert.Equal(ExpectedVersion, identity.Version);
            Assert.Equal(ExpectedGuid, identity.Guid);
            Assert.Equal(ExpectedLoadTime, identity.LoadTime);
            Assert.Matches("^sha256:[0-9a-f]{64}$", identity.CapabilityDigest);
            var expectedBuildId = Environment.GetEnvironmentVariable("RHINO_EXPECTED_PROVIDER_BUILD_ID");
            if (!string.IsNullOrWhiteSpace(expectedBuildId))
            {
                Assert.Equal(expectedBuildId, identity.ProviderBuildId);
            }
        }

        var expectedCapabilityDigest = Environment.GetEnvironmentVariable(
            ExpectedCapabilityDigestEnvironmentVariable
        );
        if (!string.IsNullOrWhiteSpace(expectedCapabilityDigest))
        {
            Assert.Matches("^sha256:[0-9a-f]{64}$", expectedCapabilityDigest);
            Assert.All(
                identities,
                identity => Assert.Equal(expectedCapabilityDigest, identity.CapabilityDigest)
            );
        }

        Assert.Equal(identities[0], identities[1]);
    }

    [Fact]
    public void IdentityProbeCanReadAnExtractedYakTargetRoot()
    {
        var outputRoot = Path.Combine(Path.GetTempPath(), "rhino-yak-identity-probe");

        Assert.Equal(
            new[]
            {
                Path.Combine(outputRoot, "net48", $"{ExpectedAssemblyName}.rhp"),
                Path.Combine(outputRoot, "net8.0", $"{ExpectedAssemblyName}.rhp"),
            },
            TargetAssemblyPaths(outputRoot)
        );
    }

    private static string[] TargetAssemblyPaths(string? outputRoot)
    {
        if (string.IsNullOrWhiteSpace(outputRoot))
        {
            return new[] { "net48", "net8.0" }
                .Select(target => Path.Combine(
                    FindNativeRoot().FullName,
                    "bin",
                    "Release",
                    target,
                    $"{ExpectedAssemblyName}.rhp"
                ))
                .ToArray();
        }

        var resolvedRoot = Path.GetFullPath(outputRoot);
        var artifactsRoot = Path.Combine(resolvedRoot, "bin", "RhinoMcpBridge");
        var artifactPaths = new[]
        {
            Path.Combine(artifactsRoot, "release_net48", $"{ExpectedAssemblyName}.rhp"),
            Path.Combine(artifactsRoot, "release_net8.0", $"{ExpectedAssemblyName}.rhp"),
        };
        if (artifactPaths.All(File.Exists))
        {
            return artifactPaths;
        }

        return new[] { "net48", "net8.0" }
            .Select(target => Path.Combine(resolvedRoot, target, $"{ExpectedAssemblyName}.rhp"))
            .ToArray();
    }

    private static DirectoryInfo FindNativeRoot()
    {
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

    private static PluginIdentity ReadIdentity(string assemblyPath)
    {
        Assert.True(File.Exists(assemblyPath), $"Missing target assembly: {assemblyPath}");

        var context = new AssemblyLoadContext(
            $"RhinoMcpBridge-{Path.GetFileName(Path.GetDirectoryName(assemblyPath))}",
            isCollectible: true
        );
        context.Resolving += (loadContext, name) =>
        {
            var dependencyPath = Path.Combine(AppContext.BaseDirectory, $"{name.Name}.dll");
            return File.Exists(dependencyPath)
                ? loadContext.LoadFromAssemblyPath(dependencyPath)
                : null;
        };

        try
        {
            var assembly = context.LoadFromAssemblyPath(assemblyPath);
            var guid = assembly.GetCustomAttribute<GuidAttribute>()?.Value;
            var pluginType = assembly.GetType(
                "RhinoMcpBridge.RhinoMcpBridgePlugin",
                throwOnError: true
            )!;
            var fixture = RuntimeHelpers.GetUninitializedObject(pluginType);
            GC.SuppressFinalize(fixture);
            var loadTime = pluginType
                .GetProperty("LoadTime", BindingFlags.Public | BindingFlags.Instance)
                ?.GetValue(fixture)
                ?.ToString();
            var name = assembly.GetName();
            var identityType = assembly.GetType(
                "RhinoMcpBridge.BridgeIdentity",
                throwOnError: true
            )!;
            var capabilityDigest = (string)identityType
                .GetMethod(
                    "ComputeCapabilityDigest",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic,
                    Type.EmptyTypes
                )!
                .Invoke(null, null)!;

            return new PluginIdentity(
                name.Name,
                name.Version?.ToString(),
                guid,
                loadTime,
                capabilityDigest,
                assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
                    .Single(attribute => attribute.Key == "RhinoProviderBuildId").Value
            );
        }
        finally
        {
            context.Unload();
        }
    }

    private sealed record PluginIdentity(
        string? Name,
        string? Version,
        string? Guid,
        string? LoadTime,
        string CapabilityDigest,
        string? ProviderBuildId
    );
}
