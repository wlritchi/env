"""Command line for pass-fwd.

wlr-pass-fwd serve        run the proxy (systemd user service)
wlr-pass-fwd socket HOST  print the local socket for HOST (for sshx)
wlr-pass-fwd export-keys  write the store recipients' public keys to the private overlay
"""

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from wlrenv.pass_fwd.dialog import DEFAULT_TIMEOUT, Pinentry
from wlrenv.pass_fwd.index import StoreIndex
from wlrenv.pass_fwd.keys import PolicySource
from wlrenv.pass_fwd.proxy import ProxyContext
from wlrenv.pass_fwd.server import HOST_RE, Server, request_socket, runtime_dir

log = logging.getLogger("pass-fwd")


def store_dir() -> Path:
    env = os.environ.get("PASSWORD_STORE_DIR")
    return Path(env) if env else Path.home() / ".password-store"


def keys_dir() -> Path:
    # The keys name devices and other keys, so they go to the private overlay,
    # not to the public repo.
    env = os.environ.get("PRIVATE_ENV_PATH")
    base = Path(env) if env else Path.home() / ".wlrenv-private"
    return base / "config" / "pass-fwd"


def gpgconf_dir(name: str) -> Path:
    output = subprocess.run(  # noqa: S603
        ["gpgconf", "--list-dirs", name],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return Path(output.strip())


async def serve(pinentry_program: str, dialog_timeout: float) -> None:
    policy = PolicySource()
    index = StoreIndex(store_dir())
    pinentry = Pinentry(pinentry_program, timeout=dialog_timeout)
    background: set[asyncio.Task[None]] = set()

    async def show_refusal(host: str) -> None:
        # Messages wait in the same queue as confirm dialogs, so that a remote
        # cannot show a message over a confirm dialog.
        async with ctx.lock:
            await pinentry.message(
                f"pass-fwd: refused request from {host}",
                f"{host} asked to decrypt a file that is not in the local "
                "password store, so pass-fwd refused it.\n\nIf the entry is "
                "new, pull the store on this machine and try again.",
            )

    def report_unknown(host: str) -> None:
        # Show at most one message at a time, so that a remote cannot fill the
        # queue with messages.
        if background:
            return
        task = asyncio.create_task(show_refusal(host))
        background.add(task)
        task.add_done_callback(background.discard)

    ctx = ProxyContext(
        policy=policy,
        lookup=lambda key: asyncio.to_thread(index.lookup, key),
        confirm=pinentry.confirm,
        recheck=lambda: asyncio.to_thread(policy.reload),
        report_unknown=report_unknown,
    )
    server = Server(runtime_dir(), gpgconf_dir("agent-extra-socket"), ctx)
    control = await server.start()
    keys = policy()
    log.info(
        "ready: decrypt keys %s, sign keys %s",
        ", ".join(k.key_id for k in keys.decrypt.values()) or "none",
        ", ".join(k.key_id for k in keys.sign.values()) or "none",
    )
    async with control:
        await control.serve_forever()


def store_recipients(store: Path) -> list[str]:
    recipients: list[str] = []
    for gpg_id in sorted(store.rglob(".gpg-id")):
        if ".git" in gpg_id.relative_to(store).parts:
            continue
        for line in gpg_id.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line and line not in recipients:
                recipients.append(line)
    return recipients


def primary_fingerprints(recipients: list[str]) -> list[str]:
    output = subprocess.run(  # noqa: S603
        ["gpg", "--batch", "--with-colons", "--list-keys", *recipients],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    fingerprints: list[str] = []
    expect_primary_fpr = False
    for line in output.splitlines():
        fields = line.split(":")
        if fields[0] == "pub":
            expect_primary_fpr = True
        elif fields[0] == "fpr" and expect_primary_fpr:
            fingerprints.append(fields[9])
            expect_primary_fpr = False
    return fingerprints


def gpg_output(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["gpg", "--batch", *args],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    ).stdout


def export_keys(out_dir: Path) -> None:
    """Write the public keys and owner trust of the store recipients.

    bin/meta/wlr-pass-fwd-home builds ~/.gnupg-fwd from these files on each
    update. Run this again after a key changes, for example after its expiry
    date is extended, and commit the result.
    """
    fingerprints = primary_fingerprints(store_recipients(store_dir()))
    armored = gpg_output(
        "--armor", "--export-options", "export-minimal", "--export", *fingerprints
    )
    trust = [
        line
        for line in gpg_output("--export-ownertrust").splitlines()
        if line.split(":", 1)[0] in fingerprints
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recipients.asc").write_text(armored)
    (out_dir / "ownertrust.txt").write_text(
        "# Written by wlr-pass-fwd export-keys. Do not edit.\n"
        + "\n".join(trust)
        + "\n"
    )
    print(
        f"wrote {len(fingerprints)} keys to {out_dir}; commit them to update other machines"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="wlr-pass-fwd")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="run the proxy")
    serve_parser.add_argument("--pinentry", default="pinentry-wayprompt")
    serve_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    socket_parser = sub.add_parser("socket", help="print the local socket for a host")
    socket_parser.add_argument("host")
    export_parser = sub.add_parser(
        "export-keys", help="write the store recipients' public keys for the repo"
    )
    export_parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.command == "serve":
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
        )
        asyncio.run(serve(args.pinentry, args.timeout))
    elif args.command == "socket":
        if not HOST_RE.fullmatch(args.host):
            sys.exit(f"pass-fwd: invalid host name: {args.host}")
        try:
            path = asyncio.run(request_socket(runtime_dir(), args.host))
        except (OSError, ValueError) as e:
            sys.exit(f"pass-fwd: not available: {e}")
        print(path)
    elif args.command == "export-keys":
        export_keys(args.output or keys_dir())
