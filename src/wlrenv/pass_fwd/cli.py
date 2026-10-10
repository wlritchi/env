"""Command line for pass-fwd.

wlr-pass-fwd serve              run the proxy (systemd user service)
wlr-pass-fwd socket HOST        print the local socket for HOST (for sshx)
wlr-pass-fwd setup-remote HOST  prepare ~/.gnupg-fwd on HOST
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

REMOTE_HOME = "$HOME/.gnupg-fwd"


def store_dir() -> Path:
    env = os.environ.get("PASSWORD_STORE_DIR")
    return Path(env) if env else Path.home() / ".password-store"


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


def setup_script(armored_keys: str, ownertrust: str) -> str:
    return f"""\
set -eu
fwd="{REMOTE_HOME}"
install -d -m 700 "$fwd"
touch "$fwd/gpg.conf"
grep -qx no-autostart "$fwd/gpg.conf" || echo no-autostart >> "$fwd/gpg.conf"
gpg --homedir "$fwd" --batch --quiet --import <<'PASS_FWD_KEYS'
{armored_keys.strip()}
PASS_FWD_KEYS
gpg --homedir "$fwd" --batch --quiet --import-ownertrust <<'PASS_FWD_TRUST'
{ownertrust.strip()}
PASS_FWD_TRUST
echo "pass-fwd: $fwd is ready on $(hostname)"
"""


def setup_remote(host: str) -> int:
    fingerprints = primary_fingerprints(store_recipients(store_dir()))
    armored = subprocess.run(  # noqa: S603
        ["gpg", "--batch", "--armor", "--export", *fingerprints],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    trust = subprocess.run(
        ["gpg", "--batch", "--export-ownertrust"],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    ownertrust = "\n".join(
        line for line in trust.splitlines() if line.split(":", 1)[0] in fingerprints
    )
    script = setup_script(armored, ownertrust)
    return subprocess.run(["ssh", host, "sh", "-s"], input=script, text=True).returncode  # noqa: S603, S607


def main() -> None:
    parser = argparse.ArgumentParser(prog="wlr-pass-fwd")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="run the proxy")
    serve_parser.add_argument("--pinentry", default="pinentry-wayprompt")
    serve_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    socket_parser = sub.add_parser("socket", help="print the local socket for a host")
    socket_parser.add_argument("host")
    setup_parser = sub.add_parser("setup-remote", help="prepare a remote machine")
    setup_parser.add_argument("host")
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
    elif args.command == "setup-remote":
        sys.exit(setup_remote(args.host))
