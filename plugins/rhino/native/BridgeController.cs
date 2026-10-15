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
            ProviderBuildId: ReadProviderBuildId(),
            ContractDigest: ComputeContractDigest(),
            CapabilityDigest: ComputeCapabilityDigest()
        );
    }

    internal static string ReadProviderBuildId()
    {
        var identities = typeof(BridgeIdentity)
            .Assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .Where(attribute => attribute.Key == "RhinoProviderBuildId")
            .Select(attribute => attribute.Value)
            .ToArray();
        if (identities.Length != 1 || !BridgeWire.IsSha256(identities[0]))
        {
            throw new InvalidDataException("Bridge Provider build identity is unavailable.");
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
    private readonly ControlPipeClient _pipeClient = new();
    private readonly NativeBridgeCommandExecutor _executor = new();
    private readonly CancellationTokenSource _shutdown = new();
    private ControlPipeSession? _activeControl;
    private BridgeServer? _activeBridge;
    private Task? _runTask;
    private BridgeControllerState _state = BridgeControllerState.WaitingForHost;
    private int _stopped;

    internal BridgeController(BridgeRegistration registration, string contractDigest)
    {
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

        ControlPipeSession? control;
        BridgeServer? bridge;
        lock (_gate)
        {
            _state = BridgeControllerState.Stopping;
            control = _activeControl;
            bridge = _activeBridge;
            _activeControl = null;
            _activeBridge = null;
        }

        _shutdown.Cancel();
        bridge?.Dispose();
        control?.Dispose();
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
                try
                {
                    SetState(BridgeControllerState.WaitingForHost);
                    control = await _pipeClient.ConnectWithRetryAsync(
                        _registration,
                        _contractDigest,
                        cancellationToken
                    ).ConfigureAwait(false);
                    SetActiveControl(control);

                    bridge = new BridgeServer(control.Lease, _executor.ExecuteAsync);
                    bridge.Start();
                    await control.ReportReadyAsync(
                        bridge.Port,
                        _registration.CapabilityDigest,
                        cancellationToken
                    ).ConfigureAwait(false);
                    SetActiveBridge(bridge);
                    SetState(BridgeControllerState.Active);
                    await control.WaitForHostDisconnectAsync(cancellationToken).ConfigureAwait(false);
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
                    ClearActive(control, bridge);
                    bridge?.Dispose();
                    control?.Dispose();
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

    private void SetActiveControl(ControlPipeSession control)
    {
        lock (_gate)
        {
            if (_stopped != 0)
            {
                control.Dispose();
                throw new OperationCanceledException(_shutdown.Token);
            }

            _activeControl = control;
        }
    }

    private void SetActiveBridge(BridgeServer bridge)
    {
        lock (_gate)
        {
            if (_stopped != 0)
            {
                bridge.Dispose();
                throw new OperationCanceledException(_shutdown.Token);
            }

            _activeBridge = bridge;
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

    private void ClearActive(ControlPipeSession? control, BridgeServer? bridge)
    {
        lock (_gate)
        {
            if (control is not null && ReferenceEquals(_activeControl, control))
            {
                _activeControl = null;
            }

            if (bridge is not null && ReferenceEquals(_activeBridge, bridge))
            {
                _activeBridge = null;
            }

            if (_stopped == 0)
            {
                _state = BridgeControllerState.WaitingForHost;
            }
        }
    }
}

internal sealed class NativeBridgeCommandExecutor
{
    private readonly CommandRegistry _registry = new();
    private readonly CommandExecutionPipeline _pipeline;
    private readonly IActiveDocumentSource _documentSource = new RhinoActiveDocumentSource();

    internal NativeBridgeCommandExecutor()
    {
        _pipeline = new CommandExecutionPipeline(_registry);
    }

    internal Task<byte[]> ExecuteAsync(
        string commandType,
        string parametersJson,
        bool includeDelta,
        bool includeHealth,
        CancellationToken cancellationToken
    )
    {
        if (cancellationToken.IsCancellationRequested)
        {
            return Task.FromCanceled<byte[]>(cancellationToken);
        }

        var completion = new TaskCompletionSource<byte[]>(TaskCreationOptions.RunContinuationsAsynchronously);
        var registration = cancellationToken.Register(() => completion.TrySetCanceled(cancellationToken));
        try
        {
            RhinoApp.InvokeOnUiThread(
                new Action(() => ExecuteOnRhinoUiThread(
                    commandType,
                    parametersJson,
                    includeDelta,
                    includeHealth,
                    cancellationToken,
                    completion
                ))
            );
        }
        catch (Exception)
        {
            completion.TrySetException(
                new NativeCommandException("nativeCommandFailed", "Rhino could not complete the command.")
            );
        }

        return AwaitAndDisposeAsync(completion.Task, registration);
    }

    private void ExecuteOnRhinoUiThread(
        string commandType,
        string parametersJson,
        bool includeDelta,
        bool includeHealth,
        CancellationToken cancellationToken,
        TaskCompletionSource<byte[]> completion
    )
    {
        if (completion.Task.IsCompleted || cancellationToken.IsCancellationRequested)
        {
            completion.TrySetCanceled(cancellationToken);
            return;
        }

        try
        {
            var parameterBytes = System.Text.Encoding.UTF8.GetBytes(parametersJson);
            using var parameterDocument = BridgeWire.ParseStrictJson(parameterBytes);
            var context = RequestContextFactory.Capture(
                _documentSource,
                Guid.NewGuid().ToString("N"),
                CancellationToken.None
            );
            var command = _registry.Get(commandType);
            HashSet<Guid>? before = null;
            if (!command.ReadOnly && (includeDelta || includeHealth))
            {
                before = SnapshotIds(context);
            }

            var result = _pipeline.Execute(commandType, context, parameterDocument.RootElement);
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
                        CaptureHealth(context, before, after)
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

    private static GeometryHealthSnapshot[] CaptureHealth(
        RequestContext context,
        HashSet<Guid> before,
        HashSet<Guid> after
    ) => after
        .Except(before)
        .Select(id => context.RhinoDocument.Objects.FindId(id))
        .Where(item => item is not null)
        .Select(item =>
        {
            var isValid = item!.Geometry.IsValidWithLog(out var log);
            return new GeometryHealthSnapshot(item.Id, isValid, log ?? string.Empty);
        })
        .ToArray();

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

internal sealed record GeometryHealthSnapshot(Guid Id, bool IsValid, string ValidityLog);

internal static class BridgePerception
{
    private const int DetailCap = 50;

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
