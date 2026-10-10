"""Confirm dialogs through the pinentry protocol."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from wlrenv.pass_fwd.index import Lookup

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0
HASH_NAMES = {2: "SHA-1", 8: "SHA-256", 9: "SHA-384", 10: "SHA-512", 11: "SHA-224"}


@dataclass(frozen=True)
class DecryptRequest:
    host: str
    lookup: Lookup


@dataclass(frozen=True)
class SignRequest:
    host: str
    key_id: str
    hash_algo: str | None
    digest: str | None


type Request = DecryptRequest | SignRequest


@dataclass(frozen=True)
class Prompt:
    title: str
    description: str
    ok: str
    cancel: str


def group(text: str, size: int = 4) -> str:
    return " ".join(text[i : i + size] for i in range(0, len(text), size))


def decrypt_prompt(request: DecryptRequest) -> Prompt:
    lookup = request.lookup
    lines = [f"{request.host} wants to decrypt:", ""]
    if lookup.current:
        lines += [f"    {path}" for path in lookup.current]
        if len(lookup.current) > 1:
            lines += [
                "",
                "WARNING: the same ciphertext is at more than one path. One of "
                "them can be a copy that hides the real entry.",
            ]
        if lookup.earlier:
            lines += ["", "Earlier names: " + ", ".join(lookup.earlier)]
    else:
        lines += [f"    {path}" for path in lookup.earlier]
        lines += ["", "This is an old version. It is not in the current store."]
    lines += ["", "After you approve, touch the YubiKey."]
    return Prompt(
        title=f"pass-fwd: decrypt for {request.host}",
        description="\n".join(lines),
        ok="Approve",
        cancel="Deny",
    )


def sign_prompt(request: SignRequest) -> Prompt:
    lines = [
        f"{request.host} wants a signature with your OpenPGP signing key "
        f"({group(request.key_id)}).",
        "",
        "THIS MACHINE CANNOT SEE WHAT WILL BE SIGNED. A git commit, a tag, an "
        "email, and a file all look the same here.",
        "",
        f"Approve only if you started a signing operation on {request.host} just now.",
    ]
    if request.digest:
        shown = request.digest[:32]
        lines += ["", f"{request.hash_algo or 'Digest'}: {group(shown)} ..."]
    lines += ["", "After you approve, touch the YubiKey."]
    return Prompt(
        title=f"pass-fwd: SIGN for {request.host}",
        description="\n".join(lines),
        ok="Sign",
        cancel="Deny",
    )


def prompt_for(request: Request) -> Prompt:
    if isinstance(request, DecryptRequest):
        return decrypt_prompt(request)
    return sign_prompt(request)


def escape(text: str) -> bytes:
    return (
        text.encode()
        .replace(b"%", b"%25")
        .replace(b"\r", b"%0D")
        .replace(b"\n", b"%0A")
    )


class Pinentry:
    """Show dialogs with a pinentry program."""

    def __init__(self, program: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.program = program
        self.timeout = timeout

    async def confirm(self, request: Request) -> bool:
        prompt = prompt_for(request)
        return await self._run(
            [
                b"SETTITLE " + escape(prompt.title),
                b"SETDESC " + escape(prompt.description),
                b"SETOK " + escape(prompt.ok),
                b"SETCANCEL " + escape(prompt.cancel),
            ],
            b"CONFIRM",
        )

    async def message(self, title: str, description: str) -> None:
        await self._run(
            [b"SETTITLE " + escape(title), b"SETDESC " + escape(description)],
            b"MESSAGE",
        )

    async def _run(self, setup: list[bytes], action: bytes) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.program,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            log.error("cannot start %s: %s", self.program, e)
            return False
        assert proc.stdin is not None
        assert proc.stdout is not None
        stdin, stdout = proc.stdin, proc.stdout

        async def reply() -> bytes:
            while line := await stdout.readline():
                if line.startswith((b"OK", b"ERR")):
                    return line
            raise ConnectionError("pinentry closed the connection")

        try:
            async with asyncio.timeout(self.timeout):
                await reply()
                for command in setup:
                    stdin.write(command + b"\n")
                    await reply()
                stdin.write(action + b"\n")
                result = await reply()
                return result.startswith(b"OK")
        except TimeoutError:
            log.info("dialog timed out")
            return False
        except (ConnectionError, OSError) as e:
            log.warning("pinentry failed: %s", e)
            return False
        finally:
            if proc.returncode is None:
                proc.kill()
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()
