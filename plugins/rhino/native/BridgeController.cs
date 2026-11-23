using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;
using RhinoMcpBridge.Functions;
using RhinoMcpBridge.Serializers;

namespace RhinoMcpBridge;

internal static class BridgeIdentity
{
    private const string ProtocolResourceName = "RhinoMcpBridge.contracts.protocol.json";

    private static readonly JsonSerializerOptions CapabilityJsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = false,
    };

    internal static BridgeRegistration CreateRegistration(uint rhinoMajor)
    {
        var assembly = typeof(BridgeIdentity).Assembly;
        var pluginId = assembly.GetCustomAttribute<GuidAttribute>()?.Value
            ?? throw new InvalidDataException("Bridge plug-in identity is unavailable.");
        var version = assembly.GetName().Version
            ?? throw new InvalidDataException("Bridge version is unavailable.");
        if (rhinoMajor != 8)
        {
            throw new InvalidDataException("Bridge requires Rhino 8.");
        }

        return new BridgeRegistration(
            SchemaVersion: 1,
            Kind: "register",
            Protocol: ControlPipeClient.PipeName,
            PluginId: pluginId,
            BridgeVersion: version.ToString(3),
            BridgeContractVersion: 1,
            RhinoMajor: rhinoMajor,
            ConnectorBuildId: ReadConnectorBuildId(),
            ContractDigest: ComputeContractDigest(),
            CapabilityDigest: ComputeCapabilityDigest()
        );
    }

    internal static string ReadConnectorBuildId()
    {
        var identities = typeof(BridgeIdentity)
            .Assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .Where(attribute => attribute.Key == "RhinoConnectorBuildId")
            .Select(attribute => attribute.Value)
            .ToArray();
        if (identities.Length != 1 || !BridgeWire.IsSha256(identities[0]))
        {
            throw new InvalidDataException("Bridge Connector build identity is unavailable.");
        }

        return identities[0]!;
    }

    internal static string ComputeContractDigest()
    {
        var assembly = typeof(BridgeIdentity).Assembly;
        using var stream = assembly.GetManifestResourceStream(ProtocolResourceName)
            ?? throw new InvalidDataException("Bridge protocol resource is unavailable.");
        using var memory = new MemoryStream();
        stream.CopyTo(memory);
        return "sha256:" + ToLowerHex(SHA256.Create().ComputeHash(memory.ToArray()));
    }

    internal static string ComputeCapabilityDigest(CommandRegistry registry)
    {
        var capabilities = registry.Commands
            .OrderBy(command => command.Name, StringComparer.Ordinal)
            .Select(command => new
            {
                name = command.Name,
                readOnly = command.ReadOnly,
                supportsDryRun = command.SupportsDryRun,
            })
            .ToArray();
        var payload = JsonSerializer.SerializeToUtf8Bytes(capabilities, CapabilityJsonOptions);
        return "sha256:" + ToLowerHex(SHA256.Create().ComputeHash(payload));
    }

    internal static string ComputeCapabilityDigest() =>
        ComputeCapabilityDigest(new CommandRegistry());

    private static string ToLowerHex(byte[] bytes) =>
        string.Concat(bytes.Select(value => value.ToString("x2")));
}

internal enum BridgeControllerState
{
    WaitingForHost,
    Active,
    Stopping,
}

internal sealed class BridgeController : IDisposable
{
    private readonly object _gate = new();
    private readonly BridgeRegistration _registration;
    private readonly string _contractDigest;
    private readonly ControlPipeClient _pipeClient;
    private readonly NativeBridgeCommandExecutor _executor = new();
    private readonly CancellationTokenSource _shutdown = new();
    private ControlPipeSession? _activeControl;
    private BridgeServer? _endpoint;
    private BridgePortBinding? _portBinding;
    private DateTimeOffset? _listeningSince;
    private NativeBridgeCommandExecutor.Session? _activeSession;
    private Task? _runTask;
    private BridgeControllerState _state = BridgeControllerState.WaitingForHost;
    private int _stopped;

    internal BridgeController(BridgeRegistration registration, string contractDigest)
        : this(registration, contractDigest, ControlPipeClient.PipeName) { }

    internal BridgeController(BridgeRegistration registration, string contractDigest, string pipeName)
    {
        _pipeClient = new ControlPipeClient(pipeName);
        _registration = registration ?? throw new ArgumentNullException(nameof(registration));
        if (!BridgeWire.IsSha256(contractDigest))
        {
            throw new ArgumentException("Bridge contract identity is invalid.", nameof(contractDigest));
        }

        _contractDigest = contractDigest;
    }

    internal BridgeControllerState State
    {
        get
        {
            lock (_gate)
            {
                return _state;
            }
        }
    }

    internal object[] ClientStatus()
    {
        lock (_gate)
            return _endpoint?.ClientStatus() ?? Array.Empty<object>();
    }

    internal object? ListenerActivity()
    {
        lock (_gate)
            return _endpoint?.ListenerActivity();
    }

    internal (int Port, string Source, DateTimeOffset StartedAt)? ListenerStatus()
    {
        lock (_gate)
            return _portBinding is { } binding && _listeningSince is { } started
                ? (binding.Port, binding.Source, started) : null;
    }

    // Called on Rhino's UI thread by the plug-in and the native management commands.
    internal bool StartEndpoint(int port, out string message)
    {
        lock (_gate)
        {
            if (_portBinding is { } current)
            {
                message = $"RhinoMCP server is already running on 127.0.0.1:{current.Port}. Run mcpstop first to change port.";
                return true;
            }

            if (_stopped != 0)
                throw new ObjectDisposedException(nameof(BridgeController));
            BridgePortBinding binding;
            var configured = System.Environment.GetEnvironmentVariable(BridgePortSelection.EnvironmentVariable);
            try
            {
                binding = BridgePortSelection.Open(port, configured);
            }
            catch (System.Net.Sockets.SocketException error) when (
                error.SocketErrorCode is System.Net.Sockets.SocketError.AddressAlreadyInUse
                    or System.Net.Sockets.SocketError.AccessDenied)
            {
                var chosen = port == 0 && int.TryParse(configured, out var environmentPort) ? environmentPort : port;
                var source = port == 0 ? BridgePortSelection.EnvironmentVariable : "Port option";
                message = $"RhinoMCP server not started: port {chosen} ({source}) is already in use, probably by another Rhino. "
                    + "Run mcpstatus there, or choose another port with: -mcpstart Port=<n>";
                return false;
            }
            catch (InvalidOperationException)
            {
                message = "RhinoMCP server not started: ports 1999-2008 are all in use. "
                    + "Stop a server with mcpstop in another Rhino, or choose a port with: -mcpstart Port=<n>";
                return false;
            }

            var endpoint = new BridgeServer();
            endpoint.StartBound(binding.Listener);
            _endpoint = endpoint;
            _portBinding = binding;
            _listeningSince = DateTimeOffset.Now;
            WindowTitleTag.Show($"MCP:{binding.Port}");
            message = $"RhinoMCP server started on 127.0.0.1:{binding.Port} ({binding.Source}).";
            if (binding.Port != 1999)
                message += $"\nMCP clients must set {BridgePortSelection.EnvironmentVariable}={binding.Port} to talk to this Rhino.";
            return true;
        }
    }

    internal string StopEndpoint()
    {
        BridgeServer? endpoint;
        ControlPipeSession? control;
        int? port;
        lock (_gate)
        {
            endpoint = _endpoint;
            port = _portBinding?.Port;
            control = _activeControl;
            _activeSession?.CloseAsync();
            _activeControl = null;
            _endpoint = null;
            _portBinding = null;
            _listeningSince = null;
        }

        endpoint?.Dispose();
        control?.Dispose();
        _executor.CloseUndoGroups();
        if (port is null)
            return "RhinoMCP server is not running.";
        WindowTitleTag.Clear();
        return $"RhinoMCP server on port {port} stopped.";
    }

    internal Task Start()
    {
        lock (_gate)
        {
            if (_runTask is not null)
            {
                return _runTask;
            }

            if (_stopped != 0)
            {
                return Task.CompletedTask;
            }

            _state = BridgeControllerState.WaitingForHost;
            _runTask = Task.Run(RunAsync);
            return _runTask;
        }
    }

    internal void Stop()
    {
        if (Interlocked.Exchange(ref _stopped, 1) != 0)
        {
            return;
        }

        lock (_gate)
            _state = BridgeControllerState.Stopping;

        _shutdown.Cancel();
        StopEndpoint();
    }

    public void Dispose()
    {
        Stop();
        _shutdown.Dispose();
    }

    private async Task RunAsync()
    {
        var cancellationToken = _shutdown.Token;
        var reconnectAttempt = 0;
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                ControlPipeSession? control = null;
                BridgeServer? bridge = null;
                NativeBridgeCommandExecutor.Session? session = null;
                try
                {
                    SetState(BridgeControllerState.WaitingForHost);
                    bool listening;
                    lock (_gate)
                        listening = _endpoint is not null;
                    if (!listening)
                    {
                        await Task.Delay(150, cancellationToken).ConfigureAwait(false);
                        continue;
                    }
                    control = await _pipeClient.ConnectWithRetryAsync(
                        _registration,
                        _contractDigest,
                        cancellationToken
                    ).ConfigureAwait(false);
                    lock (_gate)
                    {
                        // The host lookup can outlive a native stop/start. Bind to the
                        // current listener only when the verified lease is ready.
                        bridge = _endpoint;
                        if (_stopped != 0 || bridge is null)
                            throw new OperationCanceledException(cancellationToken);

                        session = _executor.OpenSession();
                        bridge.BindLease(control.Lease, session.ExecuteOwnedAsync, session.ConnectionClosed);
                        _activeSession = session;
                        _activeControl = control;
                    }
                    await control.ReportReadyAsync(
                        bridge.Port,
                        _registration.CapabilityDigest,
                        session.PreviousSessionState,
                        cancellationToken
                    ).ConfigureAwait(false);
                    SetState(BridgeControllerState.Active);
                    if (await control.ReadReleaseAsync(cancellationToken).ConfigureAwait(false))
                    {
                        await session.ConfirmReleaseAsync().ConfigureAwait(false);
                        await control.ReportReleasedAsync(cancellationToken).ConfigureAwait(false);
                    }
                    reconnectAttempt = 0;
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    break;
                }
                catch (Exception)
                {
                    // The Host will observe registration failure; normal waiting is not a Rhino error state.
                }
                finally
                {
                    // Revoke old handlers before transport disposal can cancel their responses.
                    var drain = session?.CloseAsync();
                    ClearActive(control, session);
                    if (control is not null)
                        bridge?.RevokeLease(control.Lease);
                    control?.Dispose();
                    if (drain is not null)
                    {
                        // Callback return is not proof that command side effects have settled.
                        await drain.ConfigureAwait(false);
                    }
                }

                if (cancellationToken.IsCancellationRequested)
                {
                    break;
                }

                try
                {
                    await Task.Delay(ControlPipeClient.RetryDelay(reconnectAttempt), cancellationToken)
                        .ConfigureAwait(false);
                    reconnectAttempt = Math.Min(reconnectAttempt + 1, 6);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    break;
                }
            }
        }
        finally
        {
            lock (_gate)
            {
                if (_state != BridgeControllerState.Stopping)
                {
                    _state = BridgeControllerState.Stopping;
                }
            }
        }
    }

    private void SetState(BridgeControllerState state)
    {
        lock (_gate)
        {
            if (_stopped == 0)
            {
                _state = state;
            }
        }
    }

    private void ClearActive(ControlPipeSession? control, NativeBridgeCommandExecutor.Session? session)
    {
        lock (_gate)
        {
            if (control is not null && ReferenceEquals(_activeControl, control))
            {
                _activeControl = null;
            }

            if (session is not null && ReferenceEquals(_activeSession, session))
                _activeSession = null;

            if (_stopped == 0)
            {
                _state = BridgeControllerState.WaitingForHost;
            }
        }
    }
}

internal sealed class NativeBridgeCommandExecutor
{
    private readonly Action<Action> _schedule;
    private readonly Func<string, string, bool, bool, byte[]>? _executeBody;
    private readonly CommandRegistry _registry = new();
    private readonly CommandExecutionPipeline _pipeline;
    private readonly IActiveDocumentSource _documentSource = new RhinoActiveDocumentSource();
    private readonly UndoGroups _undoGroups = new();

    internal NativeBridgeCommandExecutor() : this(callback => RhinoApp.InvokeOnUiThread(callback), null) { }

    internal NativeBridgeCommandExecutor(
        Action<Action> schedule,
        Func<string, string, bool, bool, byte[]>? executeBody
    )
    {
        _schedule = schedule;
        _executeBody = executeBody;
        _pipeline = new CommandExecutionPipeline(_registry);
    }

    internal void CloseUndoGroups()
    {
        if (_undoGroups.HasOpenGroup)
            _schedule(_undoGroups.Shutdown);
    }

    private readonly object _dispatchGate = new();
    private Session? _session;
    private bool _hasUnboundedCallbacks;

    internal Session OpenSession()
    {
        lock (_dispatchGate)
        {
            if (_session is not null && !_session.Drain.Task.IsCompleted)
            {
                throw new InvalidOperationException("The previous native session has not drained.");
            }

            return _session = new Session(this);
        }
    }

    internal sealed class Session
    {
        internal readonly NativeBridgeCommandExecutor Owner;
        internal readonly HashSet<Dispatch> Pending = new();
        internal bool HadEnteredCallbacks;
        internal readonly TaskCompletionSource<NativeDispatchDrain> Drain =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal bool Closed;
        internal bool StartedExternalExecution;

        internal Session(NativeBridgeCommandExecutor owner)
        {
            Owner = owner;
            PreviousSessionState = owner._hasUnboundedCallbacks ? "unknown" : "quiescent";
        }

        // Captured only when the preceding session has drained. This is prior-session
        // execution evidence, not the current session's outcome or resource readiness.
        internal string PreviousSessionState { get; }

        internal Task<byte[]> ExecuteAsync(
            string commandType, string parametersJson, bool includeDelta, bool includeHealth,
            CancellationToken cancellationToken
        ) => ExecuteOwnedAsync(commandType, parametersJson, includeDelta, includeHealth, "", cancellationToken);

        internal Task<byte[]> ExecuteOwnedAsync(
            string commandType, string parametersJson, bool includeDelta, bool includeHealth,
            string commandOwner, CancellationToken cancellationToken
        ) => Owner.ExecuteAsync(this, commandType, parametersJson, includeDelta, includeHealth, commandOwner, cancellationToken);

        internal void ConnectionClosed(string owner) =>
            Owner._schedule(() => Owner._undoGroups.ClientDisconnected(owner));

        internal Task<NativeDispatchDrain> CloseAsync()
        {
            lock (Owner._dispatchGate)
            {
                Closed = true;
                foreach (var dispatch in Pending.ToArray())
                {
                    Owner.CancelLocked(this, dispatch, CancellationToken.None);
                }

                CompleteDrainLocked();
                return Drain.Task;
            }
        }

        internal Task<NativeDispatchDrain> ConfirmReleaseAsync()
        {
            lock (Owner._dispatchGate)
            {
                if (ReferenceEquals(Owner._session, this) && !Closed && Pending.Count == 0
                    && PreviousSessionState == "quiescent" && !StartedExternalExecution)
                {
                    Owner._hasUnboundedCallbacks = false;
                }
                return CloseAsync();
            }
        }

        internal void CompleteDrainLocked()
        {
            if (Closed && Pending.Count == 0 && !Drain.Task.IsCompleted)
            {
                Drain.TrySetResult(new NativeDispatchDrain(HadEnteredCallbacks, Owner._hasUnboundedCallbacks));
            }
        }
    }

    internal enum DispatchState { Queued, Started, Revoked, Returned }

    internal sealed class Dispatch
    {
        internal DispatchState State;
        internal readonly TaskCompletionSource<byte[]> Completion =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
    }

    private void CancelLocked(Session session, Dispatch dispatch, CancellationToken token)
    {
        if (dispatch.State == DispatchState.Queued)
        {
            dispatch.State = DispatchState.Revoked;
            session.Pending.Remove(dispatch);
        }

        dispatch.Completion.TrySetCanceled(token);
        session.CompleteDrainLocked();
    }

    private Task<byte[]> ExecuteAsync(
        Session session, string commandType, string parametersJson, bool includeDelta, bool includeHealth,
        string commandOwner, CancellationToken cancellationToken
    )
    {
        var dispatch = new Dispatch();
        lock (_dispatchGate)
        {
            if (!ReferenceEquals(_session, session) || session.Closed || cancellationToken.IsCancellationRequested)
            {
                return Task.FromCanceled<byte[]>(new CancellationToken(true));
            }

            session.Pending.Add(dispatch);
        }

        var registration = cancellationToken.Register(() =>
        {
            lock (_dispatchGate)
            {
                CancelLocked(session, dispatch, cancellationToken);
            }
        });
        try
        {
            _schedule(() =>
            {
                lock (_dispatchGate)
                {
                    if (dispatch.State != DispatchState.Queued)
                    {
                        return;
                    }

                    if (session.Closed || cancellationToken.IsCancellationRequested)
                    {
                        CancelLocked(session, dispatch, cancellationToken);
                        return;
                    }

                    dispatch.State = DispatchState.Started;
                    session.HadEnteredCallbacks = true;
                    _hasUnboundedCallbacks |= !CompletesWithinCallback(commandType);
                    session.StartedExternalExecution |= commandType is
                        "run_command" or "execute_rhinocommon_csharp_code" or "execute_rhinoscript_python_code";
                }

                try
                {
                    ExecuteOnRhinoUiThread(commandType, parametersJson, includeDelta, includeHealth,
                        commandOwner, cancellationToken, dispatch.Completion);
                }
                finally
                {
                    lock (_dispatchGate)
                    {
                        dispatch.State = DispatchState.Returned;
                        session.Pending.Remove(dispatch);
                        session.CompleteDrainLocked();
                    }
                }
            });
        }
        catch (Exception)
        {
            lock (_dispatchGate)
            {
                // A scheduler may have entered the callback before throwing. Only a queued
                // callback can be revoked here; an entered callback owns its finally release.
                if (dispatch.State == DispatchState.Queued)
                {
                    dispatch.State = DispatchState.Revoked;
                    session.Pending.Remove(dispatch);
                }

                dispatch.Completion.TrySetException(
                    new NativeCommandException("nativeCommandFailed", "Rhino could not complete the command."));
                session.CompleteDrainLocked();
            }
        }

        return AwaitAndDisposeAsync(dispatch.Completion.Task, registration);
    }

    private bool CompletesWithinCallback(string commandType)
    {
        try
        {
            return _registry.Get(commandType).CompletesWithinCallback;
        }
        catch (NativeCommandException)
        {
            // Unrecognized handlers never gain a closed-execution guarantee.
            return false;
        }
    }

    private void ExecuteOnRhinoUiThread(
        string commandType,
        string parametersJson,
        bool includeDelta,
        bool includeHealth,
        string commandOwner,
        CancellationToken cancellationToken,
        TaskCompletionSource<byte[]> completion
    )
    {
        try
        {
            if (_executeBody is not null)
            {
                completion.TrySetResult(_executeBody(commandType, parametersJson, includeDelta, includeHealth));
                return;
            }

            var parameterBytes = System.Text.Encoding.UTF8.GetBytes(parametersJson);
            using var parameterDocument = BridgeWire.ParseStrictJson(parameterBytes);
            var context = RequestContextFactory.Capture(
                _documentSource,
                Guid.NewGuid().ToString("N"),
                CancellationToken.None
            ) with { CommandOwner = commandOwner, UndoGroupService = _undoGroups };
            var command = _registry.Get(commandType);
            context = context with { InUndoGroup = _undoGroups.PrepareCommand(context, commandType, command.ReadOnly) };
            HashSet<Guid>? before = null;
            if (!command.ReadOnly && (includeDelta || includeHealth))
            {
                before = SnapshotIds(context);
            }

            object? result = null;
            try
            {
                result = _pipeline.Execute(commandType, context, parameterDocument.RootElement);
            }
            finally
            {
                _undoGroups.FinishCommand(context, !command.ReadOnly
                    && commandType is not ("begin_undo_group" or "end_undo_group"), result);
            }
            if (before is not null && result is IDictionary<string, object?> resultObject)
            {
                var after = SnapshotIds(context);
                if (includeDelta)
                {
                    resultObject["_delta"] = BridgePerception.BuildDelta(before, after);
                }

                if (includeHealth)
                {
                    resultObject["_health"] = BridgePerception.BuildHealth(
                        BridgePerception.CaptureHealth(context.RhinoDocument, before, after)
                    );
                }
            }

            completion.TrySetResult(Serializer.Serialize(result));
        }
        catch (NativeCommandException exception)
        {
            completion.TrySetException(exception);
        }
        catch (OperationCanceledException)
        {
            completion.TrySetCanceled(cancellationToken);
        }
        catch (Exception)
        {
            completion.TrySetException(
                new NativeCommandException("nativeCommandFailed", "Rhino could not complete the command.")
            );
        }
    }

    private static HashSet<Guid> SnapshotIds(RequestContext context) =>
        NativeFunctions.Objects(context).Select(item => item.Id).ToHashSet();

    private static async Task<byte[]> AwaitAndDisposeAsync(
        Task<byte[]> task,
        CancellationTokenRegistration registration
    )
    {
        try
        {
            return await task.ConfigureAwait(false);
        }
        finally
        {
            registration.Dispose();
        }
    }
}

// Records only UI callback entry, never external/deferred engineering side-effect completion.
internal sealed record NativeDispatchDrain(bool HadEnteredCallbacks, bool HasUnboundedCallbacks);

internal sealed record GeometryHealthSnapshot(Guid Id, bool IsValid, string ValidityLog);

internal static class BridgePerception
{
    internal const int DetailCap = 50;

    internal static GeometryHealthSnapshot[] CaptureHealth(
        RhinoDoc document,
        HashSet<Guid> before,
        HashSet<Guid> after
    ) => after
        .Except(before)
        .Select(id => document.Objects.FindId(id))
        .Where(item => item is not null)
        .Select(item =>
        {
            var isValid = item!.Geometry.IsValidWithLog(out var log);
            return new GeometryHealthSnapshot(item.Id, isValid, log ?? string.Empty);
        })
        .ToArray();

    internal static Dictionary<string, object?> BuildDelta(
        IEnumerable<Guid> before,
        IEnumerable<Guid> after
    )
    {
        var beforeSet = before.ToHashSet();
        var afterSet = after.ToHashSet();
        var created = afterSet.Except(beforeSet).OrderBy(id => id.ToString("D"), StringComparer.Ordinal).ToArray();
        var deleted = beforeSet.Except(afterSet).OrderBy(id => id.ToString("D"), StringComparer.Ordinal).ToArray();
        var result = new Dictionary<string, object?>
        {
            ["created_count"] = created.Length,
            ["deleted_count"] = deleted.Length,
            ["count_before"] = beforeSet.Count,
            ["count_after"] = afterSet.Count,
            ["truncated"] = created.Length > DetailCap || deleted.Length > DetailCap,
        };

        if (created.Length <= DetailCap)
        {
            result["created_ids"] = created.Select(id => id.ToString("D")).ToArray();
        }

        if (deleted.Length <= DetailCap)
        {
            result["deleted_ids"] = deleted.Select(id => id.ToString("D")).ToArray();
        }

        return result;
    }

    internal static Dictionary<string, object?> BuildHealth(IEnumerable<GeometryHealthSnapshot> snapshots)
    {
        var created = snapshots.ToArray();
        var invalid = created.Where(snapshot => !snapshot.IsValid).ToArray();
        return new Dictionary<string, object?>
        {
            ["checked_count"] = created.Length,
            ["invalid_count"] = invalid.Length,
            ["issues"] = invalid
                .Take(DetailCap)
                .Select(snapshot => new Dictionary<string, object?>
                {
                    ["id"] = snapshot.Id.ToString("D"),
                    ["reason"] = snapshot.ValidityLog,
                })
                .ToArray(),
            ["truncated"] = invalid.Length > DetailCap,
        };
    }
}
