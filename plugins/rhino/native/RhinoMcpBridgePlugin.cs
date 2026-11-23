using Rhino.PlugIns;
using Rhino;

namespace RhinoMcpBridge;

#if NET8_0_OR_GREATER
[System.Runtime.Versioning.SupportedOSPlatform("windows")]
#endif
public sealed class RhinoMcpBridgePlugin : PlugIn
{
    private BridgeController? _controller;
    private InstanceStatusFile? _statusFile;
    private InstanceManagementPipe? _management;
    internal BridgeController? Controller => _controller;

    public RhinoMcpBridgePlugin()
    {
        Instance = this;
    }

    public static RhinoMcpBridgePlugin? Instance { get; private set; }

    public override PlugInLoadTime LoadTime => PlugInLoadTime.AtStartup;

    protected override LoadReturnCode OnLoad(ref string errorMessage)
    {
        try
        {
            var registration = BridgeIdentity.CreateRegistration((uint)RhinoApp.ExeVersion);
            _controller = new BridgeController(registration, BridgeIdentity.ComputeContractDigest(), InstanceStatusFile.ControlPipe);
            _statusFile = new InstanceStatusFile(_controller);
            _statusFile.Start();
            _controller.StartEndpoint(0, out var status);
            RhinoApp.WriteLine(status);
            _controller.Start();
            _management = new InstanceManagementPipe();
            _management.Start();
            return LoadReturnCode.Success;
        }
        catch (Exception)
        {
            errorMessage = "Rhino MCP Bridge could not initialize.";
            return LoadReturnCode.ErrorShowDialog;
        }
    }

    protected override void OnShutdown()
    {
        _management?.Dispose();
        _management = null;
        _statusFile?.Dispose();
        _statusFile = null;
        _controller?.Stop();
        _controller = null;
        base.OnShutdown();
    }
}
