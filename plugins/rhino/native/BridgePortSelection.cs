using System.Globalization;
using System.Net;
using System.Net.Sockets;

namespace RhinoMcpBridge;

internal sealed record BridgePortBinding(TcpListener Listener, int Port, string Source) : IDisposable
{
    public void Dispose() => Listener.Stop();
}

// Uses the RhinoMCP selection order. The returned listener already owns its port.
internal static class BridgePortSelection
{
    internal const string EnvironmentVariable = "RHINO_MCP_PORT";
    private const int FirstAutomaticPort = 1999;
    private const int LastAutomaticPort = 2008;

    internal static BridgePortBinding Open(int port, string? environmentPort)
    {
        if (port != 0)
            return OpenFixed(port, "Port option");
        if (int.TryParse(environmentPort, NumberStyles.Integer, CultureInfo.InvariantCulture, out var configured)
            && configured is > 0 and <= 65535)
            return OpenFixed(configured, EnvironmentVariable);

        for (var candidate = FirstAutomaticPort; candidate <= LastAutomaticPort; candidate++)
        {
            try
            {
                return OpenFixed(candidate, "automatic");
            }
            catch (SocketException error) when (error.SocketErrorCode is SocketError.AddressAlreadyInUse or SocketError.AccessDenied)
            {
                // Another Rhino instance owns this port; try the next available one.
            }
        }
        throw new InvalidOperationException("No free RhinoMCP port in range 1999-2008.");
    }

    private static BridgePortBinding OpenFixed(int port, string source)
    {
        if (port is < 1 or > 65535)
            throw new ArgumentOutOfRangeException(nameof(port), "Port must be between 1 and 65535.");
        var listener = new TcpListener(IPAddress.Loopback, port) { ExclusiveAddressUse = true };
        try
        {
            listener.Start();
            return new BridgePortBinding(listener, port, source);
        }
        catch
        {
            listener.Stop();
            throw;
        }
    }
}
