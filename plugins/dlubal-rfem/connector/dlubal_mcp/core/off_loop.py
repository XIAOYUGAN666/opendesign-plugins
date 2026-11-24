"""Run blocking tool bodies off the server's event loop.

fastmcp 2.x calls a plain (non-async) tool function directly on the event loop.
While such a function waits, on a gRPC call to RFEM or on a script's 120 s
result wait, the loop serves nothing else: not /health, not /auth-check, not
/control, not another tool call. A call RFEM never answers therefore froze the
whole server, and because /control could not answer either, the Connector
could not drain and restart it, and the connection stayed dead until the
Connector itself was restarted.

off_event_loop makes a tool async and runs its body in a worker thread. The
worker runs in a copy of the request's context, so the MCP request a tool
reads (the client application CALC sends, see client_application.py) is the
same one it would have seen on the loop.
"""

import functools

import anyio.to_thread


def off_event_loop(fn):
    """Register fn as a tool whose body runs in a worker thread.

    The wrapper keeps fn's name, docstring and signature, so fastmcp derives
    the same tool name and input and output schemas from it.
    """

    @functools.wraps(fn)
    async def tool(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

    return tool
