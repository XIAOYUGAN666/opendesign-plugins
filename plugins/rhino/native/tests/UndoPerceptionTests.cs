using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class UndoPerceptionTests
{
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void Rejected_marker_with_failed_close_reports_unknown_without_undo(bool closeThrows)
    {
        using var native = NativeTestAssembly.Load();
        var execute = native.Assembly.GetType("RhinoMcpBridge.Functions.UndoTransaction", true)!
            .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Single(method => method.Name == "Execute" && method.IsGenericMethodDefinition)
            .MakeGenericMethod(typeof(int));
        var actionRan = false;
        var undoRan = false;
        var error = Assert.Throws<TargetInvocationException>(() => execute.Invoke(null,
        [
            CancellationToken.None, "rejected marker",
            new Func<int>(() => { actionRan = true; return 1; }),
            new Func<string, uint>(_ => 4u),
            new Func<uint, bool>(_ => closeThrows ? throw new InvalidOperationException("close failed") : false),
            new Func<bool>(() => false),
            new Func<bool>(() => { undoRan = true; return true; }),
            new Action(() => { }),
        ]));
        Assert.False(actionRan);
        Assert.False(undoRan);
        Assert.Equal("undoRecordFailed", ErrorCategory(error));
    }

    [Fact]
    public void Failed_close_is_not_retried_and_never_undoes_a_foreign_record()
    {
        using var native = NativeTestAssembly.Load();
        var execute = native.Assembly.GetType("RhinoMcpBridge.Functions.UndoTransaction", true)!
            .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Single(method => method.Name == "Execute" && method.IsGenericMethodDefinition)
            .MakeGenericMethod(typeof(int));
        var closeCalls = 0;
        var foreignRecordUndone = false;
        var error = Assert.Throws<TargetInvocationException>(() => execute.Invoke(null,
        [
            CancellationToken.None, "foreign record during close",
            new Func<int>(() => 1),
            new Func<string, uint>(_ => 4u),
            // The owner rejects a close after observing a foreign record.
            new Func<uint, bool>(_ => ++closeCalls > 1),
            new Func<bool>(() => true),
            new Func<bool>(() => { foreignRecordUndone = true; return true; }),
            new Action(() => { }),
        ]));
        Assert.False(foreignRecordUndone);
        Assert.Equal(1, closeCalls);
        Assert.Equal("rollbackFailed", ErrorCategory(error));
    }

    private static object? ErrorCategory(TargetInvocationException error) =>
        error.InnerException!.GetType()
            .GetProperty("Category", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)?
            .GetValue(error.InnerException) ?? error.InnerException.GetType().Name;

    [Fact]
    public void Empty_failed_mutation_never_undoes_the_preceding_record()
    {
        using var native = NativeTestAssembly.Load();
        var execute = native.Assembly.GetType("RhinoMcpBridge.Functions.UndoTransaction", true)!
            .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Single(method => method.Name == "Execute" && method.IsGenericMethodDefinition)
            .MakeGenericMethod(typeof(int));
        var ownRecordHasEvent = false;
        var precedingRecordUndone = false;
        var calls = new List<string>();
        var arguments = new List<object?>
        {
            CancellationToken.None,
            "empty rejected mutation",
            new Func<int>(() => { calls.Add("action"); throw new InvalidOperationException("invalid geometry"); }),
            new Func<string, uint>(_ => 4u),
            new Func<uint, bool>(_ => true),
            new Func<bool>(() =>
            {
                calls.Add("undo");
                precedingRecordUndone = !ownRecordHasEvent;
                return true;
            }),
            new Action(() => calls.Add("redraw")),
        };
        arguments.Insert(5, new Func<bool>(() =>
        {
            calls.Add("record-event");
            ownRecordHasEvent = true;
            return true;
        }));
        var error = Assert.Throws<TargetInvocationException>(() => execute.Invoke(null, arguments.ToArray()));
        Assert.IsType<InvalidOperationException>(error.InnerException);
        Assert.False(precedingRecordUndone);
        Assert.Equal(new[] { "record-event", "action", "undo", "redraw" }, calls);
    }

    [Fact]
    public void Mutation_without_owned_undo_event_stops_before_action_and_global_undo()
    {
        using var native = NativeTestAssembly.Load();
        var execute = native.Assembly.GetType("RhinoMcpBridge.Functions.UndoTransaction", true)!
            .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Single(method => method.Name == "Execute" && method.IsGenericMethodDefinition)
            .MakeGenericMethod(typeof(int));
        var calls = new List<string>();
        var error = Assert.Throws<TargetInvocationException>(() => execute.Invoke(null,
        [
            CancellationToken.None,
            "rejected owner",
            new Func<int>(() => { calls.Add("action"); return 7; }),
            new Func<string, uint>(_ => { calls.Add("begin"); return 4u; }),
            new Func<uint, bool>(serial => { calls.Add($"end:{serial}"); return true; }),
            new Func<bool>(() => { calls.Add("record-event"); return false; }),
            new Func<bool>(() => { calls.Add("undo"); return true; }),
            new Action(() => calls.Add("redraw")),
        ]));
        var category = error.InnerException!.GetType()
            .GetProperty("Category", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)!
            .GetValue(error.InnerException);
        Assert.Equal("undoUnavailable", category);
        Assert.Equal(new[] { "begin", "record-event", "end:4" }, calls);
    }

    [Fact]
    public void NativeCommandPipelineAndSerializerExistForMutationAndPerceptionContracts()
    {
        using var native = NativeTestAssembly.Load();
        var pipelineType = native.Assembly.GetType(
            "RhinoMcpBridge.CommandExecutionPipeline",
            throwOnError: false
        );
        var serializerType = native.Assembly.GetType(
            "RhinoMcpBridge.Serializers.Serializer",
            throwOnError: false
        );

        Assert.NotNull(pipelineType);
        Assert.NotNull(serializerType);
        Assert.NotNull(
            pipelineType.GetMethod(
                "Execute",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance
            )
        );
        Assert.NotNull(
            serializerType.GetMethod(
                "NormalizeNegativeZero",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static
            )
        );
    }

    [Fact]
    public void Mutation_records_one_undo_and_rolls_back_when_the_action_fails()
    {
        using var native = NativeTestAssembly.Load();
        var transactionType = native.Assembly.GetType(
            "RhinoMcpBridge.Functions.UndoTransaction",
            throwOnError: false
        );
        Assert.NotNull(transactionType);
        var execute = transactionType!
            .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Single(method => method.Name == "Execute" && method.IsGenericMethodDefinition)
            .MakeGenericMethod(typeof(int));

        var successCalls = new List<string>();
        var success = execute.Invoke(
            null,
            new object?[]
            {
                CancellationToken.None,
                "test mutation",
                new Func<int>(() => { successCalls.Add("action"); return 7; }),
                new Func<string, uint>(description => { successCalls.Add($"begin:{description}"); return 4u; }),
                new Func<uint, bool>(serial => { successCalls.Add($"end:{serial}"); return true; }),
                new Func<bool>(() => true),
                new Func<bool>(() => { successCalls.Add("undo"); return true; }),
                new Action(() => successCalls.Add("redraw")),
            }
        );

        Assert.Equal(7, success);
        Assert.Equal(
            new[] { "begin:test mutation", "action", "end:4", "redraw" },
            successCalls
        );

        var failureCalls = new List<string>();
        var wrapped = Assert.Throws<TargetInvocationException>(
            () => execute.Invoke(
                null,
                new object?[]
                {
                    CancellationToken.None,
                    "test mutation",
                    new Func<int>(() => { failureCalls.Add("action"); throw new InvalidOperationException("expected"); }),
                    new Func<string, uint>(description => { failureCalls.Add($"begin:{description}"); return 9u; }),
                    new Func<uint, bool>(serial => { failureCalls.Add($"end:{serial}"); return true; }),
                    new Func<bool>(() => true),
                    new Func<bool>(() => { failureCalls.Add("undo"); return true; }),
                    new Action(() => failureCalls.Add("redraw")),
                }
            )
        );

        Assert.IsType<InvalidOperationException>(wrapped.InnerException);
        Assert.Equal(
            new[] { "begin:test mutation", "action", "end:9", "undo", "redraw" },
            failureCalls
        );
    }

    [Fact]
    public void Mutation_fails_closed_when_Rhino_cannot_start_a_dedicated_undo_record()
    {
        using var native = NativeTestAssembly.Load();
        var transactionType = native.Assembly.GetType(
            "RhinoMcpBridge.Functions.UndoTransaction",
            throwOnError: true
        )!;
        var execute = transactionType
            .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Single(method => method.Name == "Execute" && method.IsGenericMethodDefinition)
            .MakeGenericMethod(typeof(int));
        var calls = new List<string>();

        var wrapped = Assert.Throws<TargetInvocationException>(
            () => execute.Invoke(
                null,
                new object?[]
                {
                    CancellationToken.None,
                    "test mutation",
                    new Func<int>(() => { calls.Add("action"); return 7; }),
                    new Func<string, uint>(description => { calls.Add("begin"); return 0u; }),
                    new Func<uint, bool>(serial => { calls.Add("end"); return true; }),
                    new Func<bool>(() => { calls.Add("record-event"); return true; }),
                    new Func<bool>(() => { calls.Add("undo"); return true; }),
                    new Action(() => calls.Add("redraw")),
                }
            )
        );
        var category = wrapped.InnerException?.GetType()
            .GetProperty("Category", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            ?.GetValue(wrapped.InnerException) as string;

        Assert.Equal("undoUnavailable", category);
        Assert.Equal(new[] { "begin" }, calls);
    }
}
