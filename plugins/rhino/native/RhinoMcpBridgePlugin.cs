using Rhino.PlugIns;
using Rhino;

namespace RhinoMcpBridge;

public sealed class RhinoMcpBridgePlugin : PlugIn
{
    private BridgeController? _controller;

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
            _controller = new BridgeController(registration, BridgeIdentity.ComputeContractDigest());
            _controller.Start();
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
        _controller?.Stop();
        _controller = null;
        base.OnShutdown();
    }
}
