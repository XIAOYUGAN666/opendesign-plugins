using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class UndoPerceptionTests
{
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
