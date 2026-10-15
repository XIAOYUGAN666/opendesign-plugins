using System.Collections;
using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class BridgePerceptionTests
{
    [Fact]
    public void Change_delta_reports_sorted_exact_counts_and_small_id_sets()
    {
        using var native = NativeTestAssembly.Load();
        var before = new[] { Guid.Parse("00000000-0000-0000-0000-000000000002") };
        var created = Guid.Parse("00000000-0000-0000-0000-000000000001");
        var delta = BuildDelta(native, before, [before[0], created]);

        Assert.Equal(1, Value<int>(delta, "created_count"));
        Assert.Equal(0, Value<int>(delta, "deleted_count"));
        Assert.Equal(1, Value<int>(delta, "count_before"));
        Assert.Equal(2, Value<int>(delta, "count_after"));
        Assert.Equal(new[] { created.ToString("D") }, Value<IEnumerable>(delta, "created_ids").Cast<object>().Select(item => item.ToString()));
        Assert.Empty(Value<IEnumerable>(delta, "deleted_ids").Cast<object>());
        Assert.False(Value<bool>(delta, "truncated"));
    }

    [Fact]
    public void Change_delta_keeps_exact_counts_and_omits_id_arrays_over_the_cap()
    {
        using var native = NativeTestAssembly.Load();
        var before = Enumerable.Range(1, 51).Select(value => Guid.Parse($"00000000-0000-0000-0000-{value:D12}")).ToArray();
        var after = Enumerable.Range(52, 51)
            .Select(value => Guid.Parse($"10000000-0000-0000-0000-{value:D12}"))
            .ToArray();

        var delta = BuildDelta(native, before, after);

        Assert.Equal(51, Value<int>(delta, "created_count"));
        Assert.Equal(51, Value<int>(delta, "deleted_count"));
        Assert.Equal(51, Value<int>(delta, "count_before"));
        Assert.Equal(51, Value<int>(delta, "count_after"));
        Assert.True(Value<bool>(delta, "truncated"));
        Assert.False(delta.ContainsKey("created_ids"));
        Assert.False(delta.ContainsKey("deleted_ids"));
    }

    [Fact]
    public void Change_health_reports_only_invalid_created_geometry_and_caps_issue_details()
    {
        using var native = NativeTestAssembly.Load();
        var snapshots = Enumerable.Range(0, 52)
            .Select(index => CreateHealthSnapshot(
                native,
                Guid.Parse($"00000000-0000-0000-0000-{index + 1:D12}"),
                isValid: false,
                index == 0 ? "invalid geometry" : $"invalid geometry {index}"
            ))
            .ToArray();

        var health = BuildHealth(native, snapshots);

        Assert.Equal(52, Value<int>(health, "checked_count"));
        Assert.Equal(52, Value<int>(health, "invalid_count"));
        Assert.Equal(50, Value<IEnumerable>(health, "issues").Cast<object>().Count());
        Assert.True(Value<bool>(health, "truncated"));
    }

    private static IDictionary<string, object?> BuildDelta(
        NativeTestAssembly native,
        IEnumerable<Guid> before,
        IEnumerable<Guid> after
    )
    {
        var type = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.BridgePerception");
        var method = type.GetMethod("BuildDelta", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
        Assert.NotNull(method);
        return Assert.IsAssignableFrom<IDictionary<string, object?>>(method!.Invoke(null, [before, after]));
    }

    private static IDictionary<string, object?> BuildHealth(
        NativeTestAssembly native,
        IEnumerable<object> snapshots
    )
    {
        var type = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.BridgePerception");
        var method = type.GetMethod("BuildHealth", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
        Assert.NotNull(method);
        var elementType = method!.GetParameters()[0].ParameterType.GetGenericArguments()[0];
        var array = Array.CreateInstance(elementType, snapshots.Count());
        var index = 0;
        foreach (var snapshot in snapshots)
        {
            array.SetValue(snapshot, index++);
        }

        return Assert.IsAssignableFrom<IDictionary<string, object?>>(method.Invoke(null, [array]));
    }

    private static object CreateHealthSnapshot(
        NativeTestAssembly native,
        Guid id,
        bool isValid,
        string validityLog
    )
    {
        var type = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.GeometryHealthSnapshot");
        var constructor = type.GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .Single(candidate => candidate.GetParameters().Length == 3);
        return constructor.Invoke([id, isValid, validityLog]);
    }

    private static T Value<T>(IDictionary<string, object?> value, string key) => (T)value[key]!;
}
