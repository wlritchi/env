import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "bin/git/wlr-git-ssh-sign"


def command(args: list[str], cwd: Path, data: bytes | None = None) -> bytes:
    return subprocess.run(  # noqa: S603
        args, cwd=cwd, input=data, check=True, capture_output=True
    ).stdout


@pytest.fixture
def signing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    command(["git", "init", "-b", "test-branch", str(tmp_path)], tmp_path)
    command(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", "key"], tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    prompt = tools / "wayprompt"
    prompt.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path('prompt.json').write_text(json.dumps(sys.argv[1:]))\n"
        "if os.environ.get('MUTATE'):\n"
        "    pathlib.Path(os.environ['MUTATE']).write_bytes(b'changed after confirmation')\n"
        "sys.exit(int(os.environ.get('CANCEL', '0')))\n"
    )
    prompt.chmod(0o755)
    agent = tools / "ssh-add"
    agent.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
    agent.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def sign(
    repo: Path, args: list[str], data: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(WRAPPER), *args],
        cwd=repo,
        input=data,
        capture_output=True,
    )


def verify(repo: Path, payload: bytes, signature: bytes) -> None:
    (repo / "signature").write_bytes(signature)
    command(
        ["ssh-keygen", "-Y", "check-novalidate", "-n", "git", "-s", "signature"],
        repo,
        payload,
    )


def commit_payload(repo: Path, parent: str = "") -> bytes:
    (repo / "included.txt").write_text("signed tree\n")
    command(["git", "add", "included.txt"], repo)
    tree = command(["git", "write-tree"], repo).decode().strip()
    (repo / "unrelated.txt").write_text("not in signed tree\n")
    command(["git", "add", "unrelated.txt"], repo)
    parents = f"parent {parent}\n" if parent else ""
    return (
        f"tree {tree}\n{parents}author Actual Author <author@example.com> 123 +0000\n"
        "committer Other Person <other@example.com> 124 +0000\n\n"
        "Actual subject\n\nBody with trailing whitespace  \n\n"
    ).encode()


@pytest.mark.parametrize("mode", ["file", "stdin", "implicit"])
def test_exact_payload_and_commit_context(
    signing: Path, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = commit_payload(signing)
    args = ["-Y", "sign", "-n", "git", "-f", "key"]
    if mode == "file":
        (signing / "payload").write_bytes(payload)
        monkeypatch.setenv("MUTATE", "payload")
        args.append("payload")
    elif mode == "stdin":
        args.append("-")
    result = sign(signing, args, payload if mode != "file" else None)
    assert result.returncode == 0, result.stderr
    signature = (
        (signing / "payload.sig").read_bytes() if mode == "file" else result.stdout
    )
    verify(signing, payload, signature)
    prompt = json.loads((signing / "prompt.json").read_text())
    text = prompt[prompt.index("--description") + 1]
    assert "Actual Author <author@example.com>" in text
    assert "Actual subject\n\nBody with trailing whitespace  \n\n" in text
    assert "Branch: test-branch" in text
    assert f"Working directory: {signing}" in text
    assert f"Repository: {signing}" in text
    assert "included.txt" in text
    assert "unrelated.txt" not in text
    assert "--get-pin" not in prompt
    assert prompt[-4:] == ["--button-ok", "Sign", "--button-cancel", "Cancel"]


@pytest.mark.parametrize("status", ["10", "20", "1"])
def test_cancel_never_signs(
    signing: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setenv("CANCEL", status)
    result = sign(signing, ["-Y", "sign", "-n", "git", "-f", "missing-key"], b"payload")
    assert result.returncode != 0
    assert not result.stdout
    assert not result.stderr


def test_tag_and_non_utf8_bytes(signing: Path) -> None:
    payload = (
        b"object "
        + b"a" * 40
        + b"\ntype commit\ntag v1\ntagger Tag Author <tag@example.com> 123 +0000\n\nRelease\n\xff\x00\n"
    )
    result = sign(signing, ["-Ysign", "-ngit", "-fkey", "-"], payload)
    assert result.returncode == 0, result.stderr
    verify(signing, payload, result.stdout)
    prompt = json.loads((signing / "prompt.json").read_text())
    text = prompt[prompt.index("--description") + 1]
    assert "Tag: v1" in text
    assert "Tagger: Tag Author" in text
    assert "Target: commit " + "a" * 40 in text
    assert "Release" in text
    assert "Changes" not in text


def test_verification_does_not_prompt(signing: Path) -> None:
    result = sign(
        signing, ["-Y", "check-novalidate", "-n", "git", "-s", "missing"], b"payload"
    )
    assert result.returncode != 0
    assert not (signing / "prompt.json").exists()


def test_git_commit_and_tag_invocations(signing: Path) -> None:
    commit_payload(signing)
    config = [
        "git",
        "-c",
        "user.name=Test Author",
        "-c",
        "user.email=test@example.com",
        "-c",
        "gpg.format=ssh",
        "-c",
        f"gpg.ssh.program={WRAPPER}",
        "-c",
        f"user.signingkey={signing / 'key'}",
    ]
    command([*config, "commit", "-S", "-m", "Git commit message"], signing)
    prompt = json.loads((signing / "prompt.json").read_text())
    assert "Git commit message" in prompt[prompt.index("--description") + 1]
    command([*config, "tag", "-s", "v1", "-m", "Git tag message"], signing)
    prompt = json.loads((signing / "prompt.json").read_text())
    text = prompt[prompt.index("--description") + 1]
    assert "Tag: v1" in text
    assert "Git tag message" in text
    assert b"BEGIN SSH SIGNATURE" in command(["git", "cat-file", "tag", "v1"], signing)


def test_multiple_files_and_existing_signature(signing: Path) -> None:
    for name in ["first", "second"]:
        (signing / name).write_bytes(name.encode())
    args = ["-Y", "sign", "-n", "git", "-f", "key", "first", "second"]
    result = sign(signing, args)
    assert result.returncode == 0, result.stderr
    for name in ["first", "second"]:
        verify(signing, name.encode(), (signing / f"{name}.sig").read_bytes())
    previous = (signing / "first.sig").read_bytes()
    (signing / "first").write_bytes(b"changed")
    sign(signing, args, b"n\nn\n")
    assert (signing / "first.sig").read_bytes() == previous
