# One-time Dlubal MCP source import

This Connector incorporates the RFEM and shared Python source from the official
[Dlubal MCP 1.0.28 release](https://pypi.org/project/dlubal-mcp/1.0.28/).
The public source distribution and wheel were compared before import. The 30
included Python files retain their original module layout and implementation.
OpenDesign owns subsequent development of this copy.

The local entry uses the official FastMCP stdio registration and logging.
`dlubal_mcp/plugin_stdio.py`, `dlubal_mcp/setup.py` and `dlubal_mcp/sdk.py`
supply the signed installation, loopback settings, SDK preparation and
credential delivery. The RFEM API client is installed separately from its
official PyPI wheel for the detected application build. The distribution
ships the RFEM product modules and shared source only.
