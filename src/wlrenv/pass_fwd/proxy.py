"""Assuan proxy between a forwarded socket and gpg-agent's extra socket.

The proxy passes a small set of commands to the agent. It holds PKDECRYPT and
PKSIGN until the user approves them in a dialog, so that the agent does not ask
for a PIN and the card does not wait for a touch before the approval.
"""

import asyncio
import logging
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from wlrenv.pass_fwd.dialog import HASH_NAMES, DecryptRequest, Request, SignRequest
from wlrenv.pass_fwd.index import Lookup, ciphertext_key
from wlrenv.pass_fwd.keys import KeyPolicy

log = logging.getLogger(__name__)

FORBIDDEN = b"ERR 67109115 Forbidden <GPG Agent>\n"
CANCELLED = b"ERR 83886179 Operation cancelled <Pinentry>\n"
OK = b"OK\n"
INQUIRE_MAXLEN = 4096
MAX_CIPHERTEXT = 64 * 1024

GETINFO_ALLOWED = {b"VERSION", b"RESTRICTED", b"CMD_HAS_OPTION"}
SCD_ALLOWED = {b"SERIALNO", b"KEYINFO", b"GETATTR"}
FORWARDED = {b"NOP", b"HAVEKEY", b"KEYINFO", b"READKEY"}


class ProtocolError(Exception):
    pass


@dataclass
class ProxyContext:
    """State that all sessions share."""

    policy: KeyPolicy
    lookup: Callable[[bytes], Awaitable[Lookup]]
    confirm: Callable[[Request], Awaitable[bool]]
    report_unknown: Callable[[str], None] = lambda _host: None
    # One dialog and one card operation at a time, so that a dialog always
    # belongs to the operation that waits on the card.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def is_final(line: bytes) -> bool:
    return line in (b"OK\n", b"OK\r\n") or line.startswith((b"OK ", b"ERR "))


def unescape_data(line: bytes) -> bytes:
    return urllib.parse.unquote_to_bytes(line.rstrip(b"\r\n")[2:])


class Session:
    def __init__(
        self,
        host: str,
        ctx: ProxyContext,
        client_r: asyncio.StreamReader,
        client_w: asyncio.StreamWriter,
        agent_r: asyncio.StreamReader,
        agent_w: asyncio.StreamWriter,
    ) -> None:
        self.host = host
        self.ctx = ctx
        self.client_r = client_r
        self.client_w = client_w
        self.agent_r = agent_r
        self.agent_w = agent_w
        self.keygrip: str | None = None
        self.hash_algo: str | None = None
        self.digest: str | None = None

    async def run(self) -> None:
        self.client_w.write(await self._agent_line())
        await self.client_w.drain()
        while line := await self.client_r.readline():
            if not await self.handle(line):
                break

    async def _agent_line(self) -> bytes:
        line = await self.agent_r.readline()
        if not line:
            raise ProtocolError("agent closed the connection")
        return line

    async def _client_line(self) -> bytes:
        line = await self.client_r.readline()
        if not line:
            raise ProtocolError("client closed the connection")
        return line

    async def _reply(self, line: bytes) -> None:
        self.client_w.write(line)
        await self.client_w.drain()

    async def handle(self, line: bytes) -> bool:
        """Handle one client command. Return False after BYE."""
        verb, _, args = line.rstrip(b"\r\n").partition(b" ")
        verb = verb.upper()
        args = args.strip()
        first_arg = args.split(maxsplit=1)[0].upper() if args else b""

        if verb == b"BYE":
            await self.forward(line)
            return False
        if verb == b"RESET":
            self.keygrip = self.hash_algo = self.digest = None
            await self.forward(line)
        elif verb in FORWARDED:
            await self.forward(line)
        elif verb == b"GETINFO" and first_arg in GETINFO_ALLOWED:
            await self.forward(line)
        elif verb == b"SCD" and first_arg in SCD_ALLOWED:
            await self.forward(line)
        elif verb == b"OPTION":
            # Options such as display and ttyname choose where pinentry
            # appears, so they must come from the local machine. The client
            # receives OK, and the agent keeps its own values.
            if args.lower().startswith(b"agent-awareness="):
                await self.forward(line)
            else:
                await self._reply(OK)
        elif verb == b"SETKEYDESC":
            # This text comes from the remote, so it is not shown.
            await self._reply(OK)
        elif verb in (b"SETKEY", b"SIGKEY"):
            # gpg-agent uses one key slot for both commands.
            grip = args.decode(errors="replace").upper()
            if grip in self.ctx.policy.decrypt or grip in self.ctx.policy.sign:
                self.keygrip = grip
                await self.forward(line)
            else:
                log.warning("%s: refused key %s", self.host, grip)
                await self._reply(FORBIDDEN)
        elif verb == b"SETHASH":
            self._record_hash(args)
            await self.forward(line)
        elif verb == b"PKDECRYPT":
            await self.pkdecrypt(line)
        elif verb == b"PKSIGN":
            await self.pksign(line)
        else:
            log.warning(
                "%s: refused command %s", self.host, verb.decode(errors="replace")
            )
            await self._reply(FORBIDDEN)
        return True

    def _record_hash(self, args: bytes) -> None:
        self.hash_algo = self.digest = None
        parts = args.decode(errors="replace").split()
        if len(parts) != 2:
            return
        algo, digest = parts
        if algo.startswith("--hash="):
            self.hash_algo = algo.removeprefix("--hash=").upper()
        elif algo.isdigit():
            self.hash_algo = HASH_NAMES.get(int(algo), f"hash {algo}")
        self.digest = digest.upper()

    async def forward(
        self, line: bytes, inject: dict[bytes, list[bytes]] | None = None
    ) -> None:
        """Send a command to the agent and relay its replies until OK or ERR.

        Inquiries named in `inject` get the given data lines, and the client does
        not see them. Other inquiries go to the client.
        """
        self.agent_w.write(line)
        await self.agent_w.drain()
        while True:
            reply = await self._agent_line()
            if reply.startswith(b"INQUIRE "):
                keyword = reply.split()[1]
                if inject is not None and keyword in inject:
                    for data in inject[keyword]:
                        self.agent_w.write(data)
                    self.agent_w.write(b"END\n")
                    await self.agent_w.drain()
                    continue
                await self._reply(reply)
                await self._relay_inquiry()
                continue
            if inject is not None and reply.startswith(b"S INQUIRE_MAXLEN"):
                continue
            self.client_w.write(reply)
            if is_final(reply):
                await self.client_w.drain()
                return

    async def _relay_inquiry(self) -> None:
        while True:
            data = await self._client_line()
            self.agent_w.write(data)
            if data.rstrip(b"\r\n") in (b"END", b"CAN"):
                await self.agent_w.drain()
                return

    async def _read_inquiry(self, keyword: bytes) -> list[bytes] | None:
        """Ask the client for data. Return its D lines, or None if it cancels."""
        await self._reply(
            b"S INQUIRE_MAXLEN %d\nINQUIRE %s\n" % (INQUIRE_MAXLEN, keyword)
        )
        lines: list[bytes] = []
        size = 0
        while True:
            data = await self._client_line()
            stripped = data.rstrip(b"\r\n")
            if stripped == b"END":
                return lines
            if stripped == b"CAN":
                return None
            if not stripped.startswith(b"D "):
                raise ProtocolError(f"unexpected line in inquiry: {stripped[:40]!r}")
            size += len(stripped)
            if size > MAX_CIPHERTEXT:
                raise ProtocolError("inquiry data too large")
            lines.append(stripped + b"\n")

    async def pkdecrypt(self, line: bytes) -> None:
        if self.keygrip not in self.ctx.policy.decrypt:
            await self._reply(FORBIDDEN)
            return
        data = await self._read_inquiry(b"CIPHERTEXT")
        if data is None:
            await self._reply(CANCELLED)
            return
        key = ciphertext_key(b"".join(unescape_data(d) for d in data))
        lookup = await self.ctx.lookup(key) if key is not None else Lookup()
        if not lookup.found:
            log.warning("%s: refused unknown ciphertext", self.host)
            self.ctx.report_unknown(self.host)
            await self._reply(FORBIDDEN)
            return
        async with self.ctx.lock:
            approved = await self.ctx.confirm(DecryptRequest(self.host, lookup))
            log.info(
                "%s: decrypt %s %s",
                self.host,
                ", ".join(lookup.current or lookup.earlier),
                "approved" if approved else "denied",
            )
            if not approved:
                await self._reply(CANCELLED)
                return
            await self.forward(line, inject={b"CIPHERTEXT": data})

    async def pksign(self, line: bytes) -> None:
        key = self.ctx.policy.sign.get(self.keygrip or "")
        if key is None:
            await self._reply(FORBIDDEN)
            return
        request = SignRequest(self.host, key.key_id, self.hash_algo, self.digest)
        async with self.ctx.lock:
            approved = await self.ctx.confirm(request)
            log.info(
                "%s: sign %s %s",
                self.host,
                self.digest or "(no digest)",
                "approved" if approved else "denied",
            )
            if not approved:
                await self._reply(CANCELLED)
                return
            await self.forward(line)
