using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class UndoRedoStepTests
{
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void Successful_step_closes_only_its_generated_inverse_record(bool undo)
    {
        var state = new UndoState { Undo = undo };
        Assert.True(Execute(state));
        Assert.Equal(new[] { "apply", "close:12" }, state.Calls);
        Assert.Equal(0u, state.Current);
        Assert.False(state.UndoActive || state.RedoActive);
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void Existing_record_or_active_undo_is_rejected_before_applying(bool existingRecord)
    {
        var state = new UndoState();
        if (existingRecord) state.Current = 9;
        else state.UndoActive = true;
        Assert.Throws<TargetInvocationException>(() => Execute(state));
        Assert.Empty(state.Calls);
    }

    [Fact]
    public void No_step_does_not_close_any_record()
    {
        var state = new UndoState { ApplyResult = false };
        Assert.False(Execute(state));
        Assert.Equal(new[] { "apply" }, state.Calls);
    }

    [Theory]
    [InlineData("foreign")]
    [InlineData("next")]
    [InlineData("direction")]
    [InlineData("falseWithRecord")]
    public void Unverifiable_inverse_is_never_closed(string mismatch)
    {
        var state = new UndoState { Mismatch = mismatch };
        Assert.Throws<TargetInvocationException>(() => Execute(state));
        Assert.Equal(new[] { "apply" }, state.Calls);
    }

    [Theory]
    [InlineData("false")]
    [InlineData("throw")]
    [InlineData("foreignAfter")]
    public void Failed_close_is_not_reported_as_success_or_retried(string closeFailure)
    {
        var state = new UndoState { CloseFailure = closeFailure };
        Assert.Throws<TargetInvocationException>(() => Execute(state));
        Assert.Equal(new[] { "apply", "close:12" }, state.Calls);
    }

    private static bool Execute(UndoState state)
    {
        using var native = NativeTestAssembly.Load();
        var method = native.Assembly.GetType("RhinoMcpBridge.Functions.UndoRedoStep", true)!
            .GetMethod("Execute", BindingFlags.NonPublic | BindingFlags.Static)!;
        return (bool)method.Invoke(null,
        [
            state.Undo,
            new Func<bool>(state.Apply),
            new Func<uint>(() => state.Current),
            new Func<uint>(() => state.Next),
            new Func<bool>(() => state.UndoActive),
            new Func<bool>(() => state.RedoActive),
            new Func<uint, bool>(state.Close),
        ])!;
    }

    private sealed class UndoState
    {
        public bool Undo = true;
        public uint Current;
        public uint Next = 12;
        public bool UndoActive;
        public bool RedoActive;
        public bool ApplyResult = true;
        public string? Mismatch;
        public string? CloseFailure;
        public readonly List<string> Calls = new();

        public bool Apply()
        {
            Calls.Add("apply");
            if (!ApplyResult) return false;
            Current = Mismatch == "foreign" ? 44u : Next;
            Next += Mismatch == "next" ? 2u : 1u;
            UndoActive = Mismatch == "direction" ? !Undo : Undo;
            RedoActive = !UndoActive;
            return Mismatch != "falseWithRecord";
        }

        public bool Close(uint serial)
        {
            Calls.Add($"close:{serial}");
            Assert.Equal(12u, serial);
            if (CloseFailure == "throw") throw new InvalidOperationException("close failed");
            if (CloseFailure == "false") return false;
            Current = CloseFailure == "foreignAfter" ? 44u : 0u;
            UndoActive = RedoActive = false;
            return true;
        }
    }
}
