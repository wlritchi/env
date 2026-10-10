import asyncio
import contextlib
import socket
import urllib.parse
from collections.abc import AsyncIterator, Awaitable, Callable

from wlrenv.pass_fwd.dialog import DecryptRequest, Request, SignRequest
from wlrenv.pass_fwd.index import Lookup, ecdh_key
from wlrenv.pass_fwd.keys import CardKey, KeyPolicy
from wlrenv.pass_fwd.proxy import CANCELLED, FORBIDDEN, ProxyContext, Session

from .helpers import agent_ciphertext, ecdh_parts

DECRYPT_GRIP = "D6A566463C1D9CD21D6EFB77B1D3B766AA8713D0"
SIGN_GRIP = "57B7D28AC52C06DA42F5E0260679DF106DDEE770"
SOFT_GRIP = "AB5A232FAFA725F79788B6022A7C3772AC4DF9AB"
POLICY = KeyPolicy(
    decrypt={DECRYPT_GRIP: CardKey("60F5539D01361413", DECRYPT_GRIP)},
    sign={SIGN_GRIP: CardKey("E6542F8D51DCCF82", SIGN_GRIP)},
)
FOUND = Lookup(current=("Social/foo",))
DIGEST = "83A4398E9FCDA833F757160B6F00F574F0C6C1044AA679F269D004E7FD108F0A"


def encode_data(data: bytes) -> bytes:
    escaped = data.replace(b"%", b"%25").replace(b"\r", b"%0D").replace(b"\n", b"%0A")
    return b"D " + escaped + b"\n"


class FakeAgent:
    def __init__(self) -> None:
        self.lines: list[bytes] = []
        self.ciphertext: bytes | None = None

    def received(self, verb: bytes) -> bool:
        return any(line.split()[0] == verb for line in self.lines)

    async def serve(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        w.write(b"OK Pleased to meet you\n")
        await w.drain()
        while line := await r.readline():
            self.lines.append(line)
            verb = line.split()[0]
            if verb == b"PKDECRYPT":
                w.write(b"S INQUIRE_MAXLEN 4096\nINQUIRE CIPHERTEXT\n")
                await w.drain()
                data = b""
                while (d := await r.readline()) != b"END\n":
                    data += urllib.parse.unquote_to_bytes(d[2:-1])
                self.ciphertext = data
                w.write(b"S PADDING 0\nD (5:value3:key)\nOK\n")
            elif verb == b"PKSIGN":
                w.write(b"D (7:sig-val)\nOK\n")
            elif verb == b"BYE":
                w.write(b"OK closing connection\n")
                await w.drain()
                return
            else:
                w.write(b"OK\n")
            await w.drain()


class Client:
    def __init__(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        self.r = r
        self.w = w

    async def until_final(self) -> list[bytes]:
        lines = []
        while line := await self.r.readline():
            lines.append(line)
            if line == b"OK\n" or line.startswith((b"OK ", b"ERR ")):
                break
        return lines

    async def cmd(self, line: bytes) -> list[bytes]:
        self.w.write(line + b"\n")
        await self.w.drain()
        return await self.until_final()

    async def pkdecrypt(self, ciphertext: bytes) -> list[bytes]:
        self.w.write(b"PKDECRYPT\n")
        await self.w.drain()
        while (line := await self.r.readline()) != b"INQUIRE CIPHERTEXT\n":
            if line.startswith(b"ERR "):
                return [line]
        self.w.write(encode_data(ciphertext) + b"END\n")
        await self.w.drain()
        return await self.until_final()


@contextlib.asynccontextmanager
async def wired(ctx: ProxyContext, agent: FakeAgent) -> AsyncIterator[Client]:
    session_client, test_client = socket.socketpair()
    session_agent, fake_agent = socket.socketpair()
    cr, cw = await asyncio.open_unix_connection(sock=session_client)
    tr, tw = await asyncio.open_unix_connection(sock=test_client)
    ar, aw = await asyncio.open_unix_connection(sock=session_agent)
    fr, fw = await asyncio.open_unix_connection(sock=fake_agent)
    agent_task = asyncio.create_task(agent.serve(fr, fw))
    session_task = asyncio.create_task(Session("neon", ctx, cr, cw, ar, aw).run())
    assert (await tr.readline()).startswith(b"OK Pleased")
    try:
        yield Client(tr, tw)
    finally:
        tw.close()
        await asyncio.wait_for(session_task, 5)
        aw.close()
        await asyncio.wait_for(agent_task, 5)
        for writer in (cw, fw):
            writer.close()


def make_ctx(
    agent: FakeAgent,
    lookup: Lookup,
    approve: bool,
    requests: list[Request],
) -> ProxyContext:
    async def do_lookup(_key: bytes) -> Lookup:
        return lookup

    async def confirm(request: Request) -> bool:
        # The agent must not see the operation before the user approves it.
        assert not agent.received(b"PKDECRYPT")
        assert not agent.received(b"PKSIGN")
        requests.append(request)
        return approve

    return ProxyContext(policy=POLICY, lookup=do_lookup, confirm=confirm)


def run(
    test: Callable[[Client, FakeAgent], Awaitable[None]],
    lookup: Lookup = FOUND,
    approve: bool = True,
) -> list[Request]:
    requests: list[Request] = []

    async def main() -> None:
        agent = FakeAgent()
        async with wired(make_ctx(agent, lookup, approve, requests), agent) as client:
            await test(client, agent)

    asyncio.run(main())
    return requests


class TestAllowlist:
    def test_unknown_command_is_refused(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            assert await client.cmd(b"EXPORT_KEY " + SOFT_GRIP.encode()) == [FORBIDDEN]
            assert not agent.lines

        run(test)

    def test_soft_key_is_refused(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            assert await client.cmd(b"SETKEY " + SOFT_GRIP.encode()) == [FORBIDDEN]
            assert await client.cmd(b"PKDECRYPT") == [FORBIDDEN]
            assert not agent.lines

        run(test)

    def test_display_option_stays_local(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            assert await client.cmd(b"OPTION display=:1") == [b"OK\n"]
            assert await client.cmd(b"OPTION agent-awareness=2.1.0") == [b"OK\n"]
            assert agent.lines == [b"OPTION agent-awareness=2.1.0\n"]

        run(test)

    def test_scd_subcommands(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            assert await client.cmd(b"SCD SERIALNO") == [b"OK\n"]
            assert await client.cmd(b"SCD PASSWD OPENPGP.1") == [FORBIDDEN]
            assert agent.lines == [b"SCD SERIALNO\n"]

        run(test)

    def test_setkeydesc_is_not_forwarded(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            assert await client.cmd(b"SETKEYDESC Please+approve") == [b"OK\n"]
            assert not agent.lines

        run(test)


class TestDecrypt:
    def test_approved(self) -> None:
        parts = ecdh_parts()
        ciphertext = agent_ciphertext(*parts)

        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SETKEY " + DECRYPT_GRIP.encode())
            reply = await client.pkdecrypt(ciphertext)
            assert reply == [b"S PADDING 0\n", b"D (5:value3:key)\n", b"OK\n"]
            assert agent.ciphertext == ciphertext

        requests = run(test)
        assert requests == [DecryptRequest("neon", FOUND)]

    def test_ciphertext_with_escaped_bytes(self) -> None:
        ephemeral, wrapped = ecdh_parts()
        wrapped = wrapped[:5] + b"%\n\r" + wrapped[8:]
        ciphertext = agent_ciphertext(ephemeral, wrapped)
        keys: list[bytes] = []

        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SETKEY " + DECRYPT_GRIP.encode())
            await client.pkdecrypt(ciphertext)
            assert agent.ciphertext == ciphertext

        async def main() -> None:
            agent = FakeAgent()

            async def do_lookup(key: bytes) -> Lookup:
                keys.append(key)
                return Lookup(current=("x",))

            async def confirm(_request: Request) -> bool:
                return True

            ctx = ProxyContext(policy=POLICY, lookup=do_lookup, confirm=confirm)
            async with wired(ctx, agent) as client:
                await test(client, agent)

        asyncio.run(main())
        assert keys == [ecdh_key(ephemeral, wrapped)]

    def test_denied(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SETKEY " + DECRYPT_GRIP.encode())
            assert await client.pkdecrypt(agent_ciphertext(*ecdh_parts())) == [
                CANCELLED
            ]
            assert not agent.received(b"PKDECRYPT")

        assert len(run(test, approve=False)) == 1

    def test_unknown_ciphertext_is_refused_without_dialog(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SETKEY " + DECRYPT_GRIP.encode())
            assert await client.pkdecrypt(agent_ciphertext(*ecdh_parts())) == [
                FORBIDDEN
            ]
            assert not agent.received(b"PKDECRYPT")

        assert run(test, lookup=Lookup()) == []

    def test_sign_key_cannot_decrypt(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SIGKEY " + SIGN_GRIP.encode())
            assert await client.pkdecrypt(agent_ciphertext(*ecdh_parts())) == [
                FORBIDDEN
            ]

        assert run(test) == []

    def test_reset_clears_key(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SETKEY " + DECRYPT_GRIP.encode())
            await client.cmd(b"RESET")
            assert await client.pkdecrypt(agent_ciphertext(*ecdh_parts())) == [
                FORBIDDEN
            ]

        assert run(test) == []


class TestSign:
    def test_approved(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SIGKEY " + SIGN_GRIP.encode())
            await client.cmd(b"SETHASH 10 " + DIGEST.encode())
            assert await client.cmd(b"PKSIGN") == [b"D (7:sig-val)\n", b"OK\n"]

        requests = run(test)
        assert requests == [SignRequest("neon", "E6542F8D51DCCF82", "SHA-512", DIGEST)]

    def test_denied(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SIGKEY " + SIGN_GRIP.encode())
            await client.cmd(b"SETHASH 10 " + DIGEST.encode())
            assert await client.cmd(b"PKSIGN") == [CANCELLED]
            assert not agent.received(b"PKSIGN")

        assert len(run(test, approve=False)) == 1

    def test_decrypt_key_cannot_sign(self) -> None:
        async def test(client: Client, agent: FakeAgent) -> None:
            await client.cmd(b"SETKEY " + DECRYPT_GRIP.encode())
            assert await client.cmd(b"PKSIGN") == [FORBIDDEN]

        assert run(test) == []
