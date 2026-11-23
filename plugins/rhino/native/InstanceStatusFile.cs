using System.Diagnostics;
using Rhino;
using Rhino.PlugIns;
using Eto.Forms;

namespace RhinoMcpBridge;

// Instance metadata belongs to the native plug-in. File paths and window handles
// stay inside the plug-in and its Connector, outside the application projection.
internal sealed class InstanceStatusFile : IDisposable
{
    private static readonly int Pid = Process.GetCurrentProcess().Id;
    private static readonly long Creation = Process.GetCurrentProcess().StartTime.ToUniversalTime().ToFileTimeUtc();
    private static readonly Guid GrasshopperId = new("b45a29b1-4343-4035-989e-044e8580d9cf");
    private static readonly string DirectoryPath = Path.Combine(
        System.Environment.GetFolderPath(System.Environment.SpecialFolder.LocalApplicationData), "RhinoMcpBridge", "instances");
    private readonly string _path = Path.Combine(DirectoryPath, $"{Pid}.json");
    private readonly BridgeController _controller;
    private readonly UITimer _timer = new() { Interval = 1 };

    internal static string ControlPipe => $"{ControlPipeClient.PipeName}-{Pid}";
    internal static string ManagementPipe => $"rhino-mcp-management-v1-{Pid}-{Creation}";
    internal InstanceStatusFile(BridgeController controller) => _controller = controller;

    internal void Start()
    {
        _timer.Elapsed += Update;
        _timer.Start();
        Write();
    }

    public void Dispose()
    {
        _timer.Stop();
        _timer.Elapsed -= Update;
        _timer.Dispose();
        try { File.Delete(_path); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void Update(object? sender, EventArgs args) => Write();

    private void Write()
    {
        try
        {
            var doc = RhinoActiveDocumentSource.CaptureRhinoDocument();
            var listener = _controller.ListenerStatus();
            PlugIn.PlugInExists(GrasshopperId, out var loaded, out _);
            var ghName = loaded ? RhinoActiveDocumentSource.LoadedGrasshopperName() : null;
            var payload = new
            {
                pid = Pid,
                created = Creation.ToString(System.Globalization.CultureInfo.InvariantCulture),
                updatedAt = DateTimeOffset.UtcNow,
                softwareVersion = RhinoApp.Version.ToString(),
                bridgeVersion = typeof(InstanceStatusFile).Assembly.GetName().Version?.ToString(3),
                documentName = doc?.Name,
                grasshopperLoaded = loaded,
                grasshopperDocumentName = ghName,
                port = listener?.Port,
                portSource = listener?.Source,
                startedAt = listener?.StartedAt,
                clients = _controller.ClientStatus(),
                activity = _controller.ListenerActivity(),
                windowHandle = RhinoApp.MainWindowHandle().ToInt64(),
            };
            Directory.CreateDirectory(DirectoryPath);
            var temporary = _path + ".tmp";
            File.WriteAllBytes(temporary, BridgeWire.Serialize(payload));
            if (File.Exists(_path))
                File.Replace(temporary, _path, null);
            else
                File.Move(temporary, _path);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

}
