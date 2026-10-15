using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class CommandRegistryTests
{
    [Fact]
    public void DispatchHandlersMatchTheProtocolExactlyOnce()
    {
        using var native = NativeTestAssembly.Load();
        var attributeType = native.Assembly.GetType(
            "RhinoMcpBridge.McpCommandAttribute",
            throwOnError: false
        );

        Assert.NotNull(attributeType);
        var handlers = native
            .Assembly
            .GetTypes()
            .SelectMany(type =>
                type.GetMethods(
                    BindingFlags.Public
                        | BindingFlags.NonPublic
                        | BindingFlags.Static
                        | BindingFlags.Instance
                )
            )
            .Select(method =>
                (
                    Method: method,
                    Attribute: method.GetCustomAttributes(attributeType, inherit: false)
                        .SingleOrDefault()
                )
            )
            .Where(entry => entry.Attribute is not null)
            .Select(entry =>
            {
                var name = attributeType
                    .GetProperty(
                        "CommandName",
                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance
                    )
                    ?.GetValue(entry.Attribute) as string;
                return (entry.Method, Name: name);
            })
            .ToArray();

        Assert.Equal(65, handlers.Length);
        Assert.All(handlers, handler => Assert.False(string.IsNullOrWhiteSpace(handler.Name)));
        Assert.Equal(65, handlers.Select(handler => handler.Name).Distinct().Count());
        Assert.Equal(
            native.ReadProtocolCommandNames().Order(StringComparer.Ordinal),
            handlers.Select(handler => handler.Name!).Order(StringComparer.Ordinal)
        );
        Assert.All(
            handlers,
            handler =>
                Assert.Contains("RequestContext", handler.Method.GetParameters().Select(p => p.ParameterType.Name))
        );
    }

    [Fact]
    public void Arbitrary_script_commands_are_mutations_without_dry_run_support()
    {
        using var native = NativeTestAssembly.Load();
        var registryType = native.Assembly.GetType("RhinoMcpBridge.CommandRegistry", throwOnError: true)!;
        var registry = Activator.CreateInstance(registryType, new object?[] { null });
        var get = registryType.GetMethod("Get", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;

        foreach (var commandName in new[]
        {
            "execute_rhinocommon_csharp_code",
            "execute_rhinoscript_python_code",
        })
        {
            var command = get.Invoke(registry, new object?[] { commandName });
            Assert.NotNull(command);
            Assert.False((bool)command.GetType().GetProperty("ReadOnly")!.GetValue(command)!);
            Assert.False((bool)command.GetType().GetProperty("SupportsDryRun")!.GetValue(command)!);
        }
    }

    [Fact]
    public void Emits_a_stable_sha256_capability_digest_from_the_live_native_registry()
    {
        using var native = NativeTestAssembly.Load();
        var identityType = native.Assembly.GetType("RhinoMcpBridge.BridgeIdentity", throwOnError: true)!;
        var digestMethod = identityType.GetMethod(
            "ComputeCapabilityDigest",
            BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic,
            Type.EmptyTypes
        );

        Assert.NotNull(digestMethod);
        var first = (string)digestMethod!.Invoke(null, null)!;
        var second = (string)digestMethod.Invoke(null, null)!;
        Assert.Matches("^sha256:[0-9a-f]{64}$", first);
        Assert.Equal(first, second);
    }
}
