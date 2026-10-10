"""Listen on one socket per remote host and run a proxy session per connection.

The ssh client that connects to `hosts/<host>.sock` is the one that verified the
host key of <host>. Thus the socket name, not the remote, tells which machine
sends a request.
"""

import asyncio
import contextlib
import logging
import os
import re
from functools import partial
from pathlib import Path

from wlrenv.pass_fwd.proxy import ProtocolError, ProxyContext, Session

log = logging.getLogger(__name__)

HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}")


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / "pass-fwd"


def control_path(base: Path) -> Path:
    return base / "control.sock"


class Server:
    def __init__(self, base: Path, agent_socket: Path, ctx: ProxyContext) -> None:
        self.base = base
        self.agent_socket = agent_socket
        self.ctx = ctx
        self.hosts: dict[str, asyncio.Server] = {}
        self.host_lock = asyncio.Lock()

    async def start(self) -> asyncio.Server:
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.base.chmod(0o700)
        (self.base / "hosts").mkdir(mode=0o700, exist_ok=True)
        path = control_path(self.base)
        path.unlink(missing_ok=True)
        return await asyncio.start_unix_server(self.handle_control, path)

    async def ensure_host(self, host: str) -> Path:
        path = self.base / "hosts" / f"{host}.sock"
        async with self.host_lock:
            if host not in self.hosts:
                path.unlink(missing_ok=True)
                self.hosts[host] = await asyncio.start_unix_server(
                    partial(self.handle_session, host), path
                )
                log.info("listening for %s on %s", host, path)
        return path

    async def handle_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = (await reader.readline()).decode(errors="replace").strip()
            command, _, host = line.partition(" ")
            if command != "HOST" or not HOST_RE.fullmatch(host):
                writer.write(b"ERR invalid request\n")
            else:
                path = await self.ensure_host(host)
                writer.write(f"OK {path}\n".encode())
            await writer.drain()
        finally:
            writer.close()

    async def handle_session(
        self,
        host: str,
        client_r: asyncio.StreamReader,
        client_w: asyncio.StreamWriter,
    ) -> None:
        agent_w: asyncio.StreamWriter | None = None
        try:
            agent_r, agent_w = await asyncio.open_unix_connection(self.agent_socket)
            session = Session(host, self.ctx, client_r, client_w, agent_r, agent_w)
            await session.run()
        except (ProtocolError, ConnectionError, OSError) as e:
            log.info("%s: session ended: %s", host, e)
        finally:
            for writer in (client_w, agent_w):
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(ConnectionError, OSError):
                        await writer.wait_closed()


async def request_socket(base: Path, host: str) -> Path:
    """Ask a running server for the socket of a host."""
    reader, writer = await asyncio.open_unix_connection(control_path(base))
    try:
        writer.write(f"HOST {host}\n".encode())
        await writer.drain()
        reply = (await reader.readline()).decode().strip()
    finally:
        writer.close()
    if not reply.startswith("OK "):
        raise ValueError(reply or "no reply from pass-fwd")
    return Path(reply.removeprefix("OK "))
