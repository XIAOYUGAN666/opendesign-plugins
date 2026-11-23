using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Json;
using System.Threading;
using Rhino;
using RhinoMcpBridge.Commands;
#if NET8_0_OR_GREATER
using System.Runtime.Versioning;
#endif

namespace RhinoMcpBridge;

// Management stays reachable when the engineering listener is stopped.
// No command text is executed on this channel: the request selects a known action.
#if NET8_0_OR_GREATER
[SupportedOSPlatform("windows")]
#endif
internal sealed class InstanceManagementPipe : IDisposable
{
    private readonly CancellationTokenSource _shutdown = new();
    private NamedPipeServerStream? _activePipe;
    private int _disposed;

    internal void Start() => _ = ServeAsync(_shutdown.Token);

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
            return;
        _shutdown.Cancel();
        Interlocked.Exchange(ref _activePipe, null)?.Dispose();
        _shutdown.Dispose();
    }

    private async Task ServeAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                using var pipe = CreatePipe();
                Interlocked.Exchange(ref _activePipe, pipe);
                if (cancellationToken.IsCancellationRequested)
                    return;
                await pipe.WaitForConnectionAsync(cancellationToken).ConfigureAwait(false);
                using var readTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                readTimeout.CancelAfter(TimeSpan.FromSeconds(3));
                var request = await BridgeWire.ReadFramePayloadAsync(pipe, BridgeWire.MaxControlFrameSize,
                    readTimeout.Token, allowCleanEof: true).ConfigureAwait(false);
                if (request is null)
                    continue;
                using var document = BridgeWire.ParseStrictJson(request);
                var reply = await RunOnRhinoThread(document.RootElement, cancellationToken).ConfigureAwait(false);
                await BridgeWire.WriteJsonFrameAsync(pipe, reply, BridgeWire.MaxControlFrameSize, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception) when (!cancellationToken.IsCancellationRequested)
            {
                // A stale client, invalid frame or unavailable pipe must not stop the loop.
                try { await Task.Delay(100, cancellationToken).ConfigureAwait(false); }
                catch (OperationCanceledException) { return; }
            }
            catch (Exception) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            finally
            {
                Interlocked.Exchange(ref _activePipe, null);
            }
        }
    }

    private static NamedPipeServerStream CreatePipe()
    {
#if NET48
        using var user = WindowsIdentity.GetCurrent();
        var security = new PipeSecurity();
        var sid = user.User ?? throw new InvalidOperationException("Windows user identity unavailable.");
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        security.AddAccessRule(new PipeAccessRule(sid, PipeAccessRights.FullControl, AccessControlType.Allow));
        return new NamedPipeServerStream(InstanceStatusFile.ManagementPipe, PipeDirection.InOut, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 65536, 65536, security);
#else
        return new NamedPipeServerStream(InstanceStatusFile.ManagementPipe, PipeDirection.InOut, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly, 65536, 65536);
#endif
    }

    private static Task<object> RunOnRhinoThread(JsonElement request, CancellationToken cancellationToken)
    {
        var completion = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
        RhinoApp.InvokeOnUiThread(new Action(() =>
        {
            if (cancellationToken.IsCancellationRequested)
            {
                completion.TrySetCanceled();
                return;
            }
            try
            {
                completion.TrySetResult(Execute(request));
            }
            catch (Exception)
            {
                completion.TrySetResult(new { success = false, output = "Rhino could not complete the requested management action." });
            }
        }));
        return completion.Task;
    }

    private static object Execute(JsonElement request)
    {
        if (request.ValueKind != JsonValueKind.Object || !request.TryGetProperty("action", out var action)
            || action.ValueKind != JsonValueKind.String)
            return InvalidRequest();

        switch (action.GetString())
        {
            case "mcpstart":
                if (request.EnumerateObject().Any(field => field.Name is not ("action" or "port")))
                    return InvalidRequest();
                var port = 0;
                if (request.TryGetProperty("port", out var requested) &&
                    (!requested.TryGetInt32(out port) || port < 0 || port > 65535))
                    return InvalidRequest();
                var started = BridgeCommandActions.Start(port);
                return new { success = started.Success, output = started.Output };
            case "mcpstop" when request.EnumerateObject().Count() == 1:
                return new { success = true, output = BridgeCommandActions.Stop() };
            case "mcpversion" when request.EnumerateObject().Count() == 1:
                return new { success = true, output = BridgeCommandActions.Version() };
            case "mcptest":
                return RunSelfTests(request);
            default:
                return InvalidRequest();
        }
    }

    private static object RunSelfTests(JsonElement request)
    {
        var visual = false;
        var grasshopper = false;
        var delay = 500;
        foreach (var field in request.EnumerateObject())
        {
            switch (field.Name)
            {
                case "action":
                    break;
                case "visual" when field.Value.ValueKind is JsonValueKind.True or JsonValueKind.False:
                    visual = field.Value.GetBoolean();
                    break;
                case "grasshopper" when field.Value.ValueKind is JsonValueKind.True or JsonValueKind.False:
                    grasshopper = field.Value.GetBoolean();
                    break;
                case "delay" when field.Value.TryGetInt32(out delay) && delay is >= 0 and <= 5000:
                    break;
                default:
                    return InvalidRequest();
            }
        }

        var document = RhinoActiveDocumentSource.CaptureRhinoDocument();
        if (document is null)
            return new { success = false, output = "No active Rhino document" };
        var output = new List<string>();
        var result = MCPTestCommand.RunTests(document, visual, grasshopper, delay, output.Add);
        return new { success = result == Rhino.Commands.Result.Success, output = string.Join("\n", output) };
    }

    private static object InvalidRequest() => new { success = false, output = "Invalid Rhino management request." };
}
