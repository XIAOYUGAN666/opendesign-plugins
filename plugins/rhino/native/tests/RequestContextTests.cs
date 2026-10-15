using System.Reflection;
using System.Runtime.CompilerServices;
using Xunit;

namespace RhinoMcpBridge.Tests;

[Collection("NativeIdentityOnlyFixtures")]
public sealed class RequestContextTests
{
    [Fact]
    public void RequestContextIsAnImmutableSnapshotOfRhinoAndGrasshopperState()
    {
        using var native = NativeTestAssembly.Load();
        var contextType = native.Assembly.GetType(
            "RhinoMcpBridge.RequestContext",
            throwOnError: false
        );

        Assert.NotNull(contextType);
        Assert.True(contextType.IsSealed);
        var properties = contextType.GetProperties(
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance
        );
        var requiredProperties = new[]
        {
            "RhinoDocument",
            "GrasshopperDocument",
            "GrasshopperCanvas",
            "AbsoluteTolerance",
            "AngleToleranceRadians",
            "ModelUnitSystem",
            "RequestId",
            "CancellationToken",
        };

        Assert.All(requiredProperties, name => Assert.Contains(properties, property => property.Name == name));
        Assert.All(
            properties.Where(property => property.SetMethod is not null),
            property =>
                Assert.Contains(
                    typeof(System.Runtime.CompilerServices.IsExternalInit),
                    property.SetMethod!.ReturnParameter.GetRequiredCustomModifiers()
                )
        );
    }

    [Fact]
    public void RequestContextCaptureFactoryHasAReplaceableSourceForDocumentSwitchTests()
    {
        using var native = NativeTestAssembly.Load();
        var factoryType = native.Assembly.GetType(
            "RhinoMcpBridge.RequestContextFactory",
            throwOnError: false
        );

        Assert.NotNull(factoryType);
        Assert.NotNull(
            factoryType.GetMethod(
                "Capture",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static
            )
        );
    }

    [Fact]
    public void Active_document_switch_after_capture_does_not_retarget_the_in_flight_context()
    {
        using var native = NativeTestAssembly.Load();
        var snapshotType = native.Assembly.GetType("RhinoMcpBridge.ActiveDocumentSnapshot", throwOnError: false);
        Assert.NotNull(snapshotType);

        var documentA = Uninitialized(native, "RhinoCommon", "Rhino.RhinoDoc");
        var documentB = Uninitialized(native, "RhinoCommon", "Rhino.RhinoDoc");
        object? grasshopperA = null;
        object? grasshopperB = null;
        var canvasA = Uninitialized(native, "Grasshopper", "Grasshopper.GUI.Canvas.GH_Canvas");
        var canvasB = Uninitialized(native, "Grasshopper", "Grasshopper.GUI.Canvas.GH_Canvas");
        var snapshotConstructor = snapshotType!
            .GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .Single(candidate => candidate.GetParameters().Length == 6);
        var unitSystemType = snapshotConstructor.GetParameters()[5].ParameterType;
        var snapshotA = snapshotConstructor.Invoke(
            new object?[] { documentA, grasshopperA, canvasA, 0.01, 0.02, Enum.ToObject(unitSystemType, 1) }
        );
        var snapshotB = snapshotConstructor.Invoke(
            new object?[] { documentB, grasshopperB, canvasB, 0.03, 0.04, Enum.ToObject(unitSystemType, 2) }
        );

        var factoryType = native.Assembly.GetType("RhinoMcpBridge.RequestContextFactory", throwOnError: true)!;
        var createContext = factoryType.GetMethod(
            "CreateContext",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static
        )!;
        var firstCall = createContext.Invoke(
            null,
            new object?[] { snapshotA, null, "request-a", CancellationToken.None }
        );

        // A later active-state snapshot represents B, while the in-flight context remains bound to A.
        Assert.Same(documentA, Property(firstCall!, "RhinoDocument"));
        Assert.Null(Property(firstCall!, "GrasshopperDocument"));
        Assert.Same(canvasA, Property(firstCall!, "GrasshopperCanvas"));
        Assert.Equal(0.01, Property(firstCall!, "AbsoluteTolerance"));
        Assert.Equal(0.02, Property(firstCall!, "AngleToleranceRadians"));

        var nextCall = createContext.Invoke(
            null,
            new object?[] { snapshotB, null, "request-b", CancellationToken.None }
        );
        Assert.Same(documentB, Property(nextCall!, "RhinoDocument"));
        Assert.Null(Property(nextCall!, "GrasshopperDocument"));
        Assert.Same(canvasB, Property(nextCall!, "GrasshopperCanvas"));
        Assert.Equal(0.03, Property(nextCall!, "AbsoluteTolerance"));
        Assert.Equal(0.04, Property(nextCall!, "AngleToleranceRadians"));
        GC.Collect();
        GC.WaitForPendingFinalizers();
    }

    [Fact]
    public void Capture_without_a_Rhino_document_returns_the_typed_no_active_document_error()
    {
        using var native = NativeTestAssembly.Load();
        var snapshotType = native.Assembly.GetType("RhinoMcpBridge.ActiveDocumentSnapshot", throwOnError: true)!;
        var snapshotConstructor = snapshotType
            .GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .Single(candidate => candidate.GetParameters().Length == 6);
        var unitSystemType = snapshotConstructor.GetParameters()[5].ParameterType;
        var snapshot = snapshotConstructor.Invoke(
            new object?[] { null, null, null, 0, 0, Enum.ToObject(unitSystemType, 0) }
        );
        var factoryType = native.Assembly.GetType("RhinoMcpBridge.RequestContextFactory", throwOnError: true)!;
        var createContext = factoryType.GetMethod(
            "CreateContext",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static
        )!;
        var wrapped = Assert.Throws<TargetInvocationException>(
            () => createContext.Invoke(
                null,
                new object?[] { snapshot, null, "request-no-document", CancellationToken.None }
            )
        );
        var category = wrapped.InnerException?.GetType()
            .GetProperty("Category", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            ?.GetValue(wrapped.InnerException) as string;

        Assert.Equal("noActiveDocument", category);
    }

    private static object Uninitialized(NativeTestAssembly native, string assemblyName, string typeName)
    {
        var type = native.LoadDependency(assemblyName).GetType(typeName, throwOnError: true)!;
        var fixture = RuntimeHelpers.GetUninitializedObject(type);
        // Identity-only fixtures never acquired native resources. Their software
        // finalizers must not run against an absent Rhino process.
        GC.SuppressFinalize(fixture);
        return fixture;
    }

    private static object? Property(object instance, string name) =>
        instance.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            ?.GetValue(instance);

}

[CollectionDefinition("NativeIdentityOnlyFixtures", DisableParallelization = true)]
public sealed class NativeIdentityOnlyFixturesCollection { }
