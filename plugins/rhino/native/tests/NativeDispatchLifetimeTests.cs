using System.Reflection;
using Xunit;

namespace RhinoMcpBridge.Tests;

public sealed class NativeDispatchLifetimeTests
{
    [Fact]
    public async Task Queued_cancellation_prevents_delayed_callback_side_effects()
    {
        using var native = NativeTestAssembly.Load();
        Action? queued = null;
        var effects = 0;
        var executor = Create(native, callback => queued = callback, () => { effects++; return [1]; });
        var session = Open(executor);
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);
        var response = Execute(session, cancellation.Token);
        cancellation.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => response);
        await Close(session).WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
        queued!();
        Assert.False(HadEnteredCallbacks(session));
        Assert.Equal(0, effects);
    }

    [Fact]
    public async Task Started_cancellation_keeps_drain_pending_until_callback_really_returns()
    {
        using var native = NativeTestAssembly.Load();
        Action? queued = null;
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        var executor = Create(native, callback => queued = callback, () =>
        {
            entered.Set();
            if (!release.Wait(TimeSpan.FromSeconds(10))) throw new TimeoutException();
            return [1];
        });
        var session = Open(executor);
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);
        var response = Execute(session, cancellation.Token, "run_command");
        var callbackTask = Task.Run(() => queued!(), TestContext.Current.CancellationToken);
        try
        {
            Assert.True(entered.Wait(TimeSpan.FromSeconds(5), TestContext.Current.CancellationToken));
            cancellation.Cancel();
            await Assert.ThrowsAnyAsync<OperationCanceledException>(() => response);
            var drain = Close(session);
            Assert.False(drain.IsCompleted, "Caller cancellation must not stand in for native callback return.");
            release.Set();
            await callbackTask.WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
            await drain.WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
            Assert.True(HadEnteredCallbacks(session));
            var next = Open(executor);
            Assert.Equal("unknown", PreviousState(next));
            await Close(next);
        }
        finally { release.Set(); await callbackTask; }
    }

    [Fact]
    public async Task Closed_old_session_refuses_late_handlers_even_after_next_session_opens()
    {
        using var native = NativeTestAssembly.Load();
        var scheduled = new Queue<Action>();
        var effects = 0;
        var executor = Create(native, scheduled.Enqueue, () => { effects++; return [7]; });
        var old = Open(executor);
        var oldResponse = Execute(old, TestContext.Current.CancellationToken);
        await Close(old).WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
        var next = Open(executor);
        var late = Execute(old, TestContext.Current.CancellationToken);
        Assert.True(late.IsCompleted, "A revoked handler must reject before UI dispatch.");
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => late);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => oldResponse);
        var current = Execute(next, TestContext.Current.CancellationToken);
        while (scheduled.TryDequeue(out var callback)) callback();
        Assert.Equal(new byte[] { 7 }, await current);
        Assert.Equal(1, effects);
        await Close(next);
    }

    [Fact]
    public async Task Next_session_cannot_open_until_started_callback_returns()
    {
        using var native = NativeTestAssembly.Load();
        Action? queued = null;
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        var executor = Create(native, callback => queued = callback, () =>
        {
            entered.Set();
            if (!release.Wait(TimeSpan.FromSeconds(10))) throw new TimeoutException();
            return [1];
        });
        var old = Open(executor);
        var response = Execute(old, TestContext.Current.CancellationToken);
        var callbackTask = Task.Run(() => queued!(), TestContext.Current.CancellationToken);
        try
        {
            Assert.True(entered.Wait(TimeSpan.FromSeconds(5), TestContext.Current.CancellationToken));
            var drain = Close(old);
            Assert.Throws<InvalidOperationException>(() => Open(executor));
            release.Set();
            await callbackTask.WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
            await drain.WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
            await Assert.ThrowsAnyAsync<OperationCanceledException>(() => response);
            await Close(Open(executor));
        }
        finally { release.Set(); await callbackTask; }
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task Scheduler_and_body_failures_release_the_dispatch_for_next_session(bool schedulerFails)
    {
        using var native = NativeTestAssembly.Load();
        var executor = Create(native,
            callback => { if (schedulerFails) throw new InvalidOperationException(); callback(); },
            () => throw new InvalidOperationException());
        var session = Open(executor);
        await Assert.ThrowsAnyAsync<Exception>(() => Execute(session, TestContext.Current.CancellationToken));
        await Close(session).WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
        await Close(Open(executor)).WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
    }

    [Fact]
    public async Task Scheduler_failure_after_enqueue_revokes_the_delayed_callback()
    {
        using var native = NativeTestAssembly.Load();
        Action? queued = null;
        var effects = 0;
        var executor = Create(native, callback =>
        {
            queued = callback;
            throw new InvalidOperationException();
        }, () => { effects++; return [1]; });
        var session = Open(executor);
        await Assert.ThrowsAnyAsync<Exception>(() => Execute(session, TestContext.Current.CancellationToken));
        await Close(session).WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
        var next = Open(executor);
        queued!();
        Assert.Equal(0, effects);
        Assert.False(HadEnteredCallbacks(session));
        await Close(next);
    }

    [Fact]
    public async Task Scheduler_failure_after_callback_entry_does_not_release_running_body()
    {
        using var native = NativeTestAssembly.Load();
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        Task? callbackTask = null;
        var executor = Create(native, callback =>
        {
            callbackTask = Task.Run(callback, TestContext.Current.CancellationToken);
            if (!entered.Wait(TimeSpan.FromSeconds(5), TestContext.Current.CancellationToken)) throw new TimeoutException();
            throw new InvalidOperationException();
        }, () =>
        {
            entered.Set();
            if (!release.Wait(TimeSpan.FromSeconds(10))) throw new TimeoutException();
            return [1];
        });
        var session = Open(executor);
        try
        {
            await Assert.ThrowsAnyAsync<Exception>(() => Execute(session, TestContext.Current.CancellationToken));
            Assert.True(entered.IsSet);
            var drain = Close(session);
            Assert.False(drain.IsCompleted);
            Assert.Throws<InvalidOperationException>(() => Open(executor));
            release.Set();
            await callbackTask!.WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
            await drain.WaitAsync(TimeSpan.FromSeconds(2), TestContext.Current.CancellationToken);
            Assert.True(HadEnteredCallbacks(session));
            await Close(Open(executor));
        }
        finally { release.Set(); if (callbackTask is not null) await callbackTask; }
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task Drain_summary_allocation_stays_bounded_after_many_callbacks(bool repeatedClose)
    {
        using var native = NativeTestAssembly.Load();
        var executor = Create(native, callback => callback(), () => Array.Empty<byte>());
        // Warm the closing/reflection path outside the measured session.
        await Close(Open(executor));
        var session = Open(executor);
        for (var index = 0; index < 4096; index++)
        {
            await Execute(session, TestContext.Current.CancellationToken);
        }

        // Bind before sampling: the reflection invoker can allocate its lazy
        // dispatch stub independently of the production drain's storage.
        var close = session.GetType()
            .GetMethod("CloseAsync", BindingFlags.Instance | BindingFlags.NonPublic)!
            .CreateDelegate<Func<Task>>(session);
        if (repeatedClose) await close();
        var before = GC.GetAllocatedBytesForCurrentThread();
        var drain = close();
        var allocated = GC.GetAllocatedBytesForCurrentThread() - before;

        Assert.True(allocated < 8192,
            $"Closing must use bounded summary storage; allocated {allocated} bytes (repeated={repeatedClose}).");
        Assert.True(drain.IsCompletedSuccessfully);
        var summary = drain.GetType().GetProperty("Result")!.GetValue(drain);
        var repeated = close();
        Assert.Same(drain, repeated);
        Assert.Same(summary, repeated.GetType().GetProperty("Result")!.GetValue(repeated));
    }

    [Theory]
    [InlineData("describe_capabilities")]
    [InlineData("get_document_summary")]
    [InlineData("get_objects")]
    [InlineData("get_commands")]
    [InlineData("get_selected_objects_info")]
    [InlineData("analyze_objects")]
    [InlineData("measure_objects")]
    [InlineData("section_profile")]
    [InlineData("capture_viewport")]
    [InlineData("boolean_union")]
    [InlineData("boolean_difference")]
    [InlineData("boolean_intersection")]
    [InlineData("loft")]
    [InlineData("extrude_curve")]
    [InlineData("sweep1")]
    [InlineData("offset_curve")]
    [InlineData("pipe")]
    [InlineData("create_objects")]
    [InlineData("project_curve")]
    [InlineData("intersect_curves")]
    [InlineData("split_curve")]
    [InlineData("create_planar_region")]
    [InlineData("modify_object")]
    [InlineData("modify_objects")]
    [InlineData("get_or_set_current_layer")]
    [InlineData("select_objects")]
    [InlineData("undo")]
    [InlineData("redo")]
    [InlineData("get_object_info")]
    [InlineData("get_object_attributes")]
    [InlineData("update_object_attributes")]
    [InlineData("create_layer")]
    [InlineData("delete_layer")]
    [InlineData("create_object")]
    [InlineData("delete_object")]
    [InlineData("gh_get_document_info")]
    public async Task Closed_handler_return_allows_next_session_to_report_quiescent(string command)
    {
        using var native = NativeTestAssembly.Load();
        var executor = Create(native, callback => callback(), () => [1]);
        var session = Open(executor);
        Assert.Equal("quiescent", PreviousState(session));
        await Execute(session, TestContext.Current.CancellationToken, command);
        await Close(session);
        var next = Open(executor);
        Assert.Equal("quiescent", PreviousState(next));
        await Close(next);
    }

    [Theory]
    [InlineData("run_command")]
    [InlineData("execute_rhinocommon_csharp_code")]
    [InlineData("execute_rhinoscript_python_code")]
    [InlineData("gh_run_solution")]
    public async Task Open_world_callback_entry_stays_unknown_across_future_sessions(string command)
    {
        using var native = NativeTestAssembly.Load();
        var executor = Create(native, callback => callback(), () => [1]);
        var session = Open(executor);
        await Execute(session, TestContext.Current.CancellationToken, command);
        // Evidence for this session was captured before it accepted any callback.
        Assert.Equal("quiescent", PreviousState(session));
        await Close(session);
        var next = Open(executor);
        Assert.Equal("unknown", PreviousState(next));
        await Execute(next, TestContext.Current.CancellationToken, "describe_capabilities");
        await Close(next);
        var third = Open(executor);
        Assert.Equal("unknown", PreviousState(third));
        await Close(third);
    }

    [Fact]
    public async Task Open_world_callback_failure_does_not_erase_unknown_entry_evidence()
    {
        using var native = NativeTestAssembly.Load();
        var executor = Create(native, callback => callback(), () => throw new InvalidOperationException());
        var session = Open(executor);
        await Assert.ThrowsAnyAsync<Exception>(() => Execute(session, TestContext.Current.CancellationToken, "run_command"));
        await Close(session);
        var next = Open(executor);
        Assert.Equal("unknown", PreviousState(next));
        await Close(next);
    }

    [Fact]
    public async Task Queued_open_world_callback_revocation_keeps_next_session_quiescent()
    {
        using var native = NativeTestAssembly.Load();
        Action? queued = null;
        var effects = 0;
        var executor = Create(native, callback => queued = callback, () => { effects++; return [1]; });
        var session = Open(executor);
        var response = Execute(session, TestContext.Current.CancellationToken, "run_command");
        await Close(session);
        var next = Open(executor);
        queued!();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => response);
        Assert.Equal(0, effects);
        Assert.Equal("quiescent", PreviousState(next));
        await Close(next);
    }

    [Fact]
    public async Task Only_audited_registry_handlers_can_leave_prior_execution_quiescent()
    {
        using var native = NativeTestAssembly.Load();
        var registryType = BridgeTestSupport.RequireType(native, "RhinoMcpBridge.CommandRegistry");
        var registry = Activator.CreateInstance(registryType, new object?[] { null })!;
        var commands = (System.Collections.IEnumerable)registryType.GetProperty("Commands")!.GetValue(registry)!;
        var closedHandlers = new HashSet<string>(StringComparer.Ordinal)
        {
            "describe_capabilities", "get_document_summary", "get_objects", "get_object_info",
            "get_commands", "get_selected_objects_info", "analyze_objects",
            "measure_objects", "section_profile", "capture_viewport",
            "boolean_union", "boolean_difference", "boolean_intersection",
            "loft", "extrude_curve", "sweep1", "offset_curve", "pipe", "create_objects",
            "project_curve", "intersect_curves", "split_curve", "create_planar_region",
            "modify_object", "modify_objects", "get_or_set_current_layer", "select_objects", "undo", "redo",
            "get_object_attributes", "update_object_attributes",
            "create_layer", "delete_layer", "create_object", "delete_object", "gh_get_document_info",
        };
        foreach (var command in commands)
        {
            var name = (string)command.GetType().GetProperty("Name")!.GetValue(command)!;
            var executor = Create(native, callback => callback(), () => [1]);
            var session = Open(executor);
            await Execute(session, TestContext.Current.CancellationToken, name);
            await Close(session);
            var next = Open(executor);
            Assert.Equal(closedHandlers.Contains(name) ? "quiescent" : "unknown", PreviousState(next));
            await Close(next);
        }
    }

    private static string PreviousState(object session) =>
        (string)session.GetType().GetProperty("PreviousSessionState", BindingFlags.Instance | BindingFlags.NonPublic)
            !.GetValue(session)!;

    private static bool HadEnteredCallbacks(object session)
    {
        var task = Close(session);
        Assert.True(task.IsCompletedSuccessfully);
        var summary = task.GetType().GetProperty("Result")!.GetValue(task)!;
        return (bool)summary.GetType().GetProperty("HadEnteredCallbacks")!.GetValue(summary)!;
    }

    private static object Create(NativeTestAssembly native, Action<Action> schedule, Func<byte[]> body) =>
        Activator.CreateInstance(BridgeTestSupport.RequireType(native, "RhinoMcpBridge.NativeBridgeCommandExecutor"),
            BindingFlags.Instance | BindingFlags.NonPublic, null,
            [schedule, new Func<string, string, bool, bool, byte[]>((_, _, _, _) => body())], null)!;

    private static object Open(object executor) => Invoke(executor, "OpenSession")!;
    private static Task Close(object session) => (Task)Invoke(session, "CloseAsync")!;
    private static Task<byte[]> Execute(object session, CancellationToken token, string command = "test") =>
        (Task<byte[]>)Invoke(session, "ExecuteAsync", command, "{}", false, false, token)!;
    private static object? Invoke(object target, string method, params object[] arguments)
    {
        try { return target.GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(target, arguments); }
        catch (TargetInvocationException exception) when (exception.InnerException is not null)
        { System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(exception.InnerException).Throw(); throw; }
    }
}
