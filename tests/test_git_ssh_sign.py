import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pytest_mock import MockerFixture

WRAPPER = Path(__file__).resolve().parents[1] / "bin/git/wlr-git-ssh-sign"


def command(args: list[str], cwd: Path, data: bytes | None = None) -> bytes:
    return subprocess.run(  # noqa: S603
        args, cwd=cwd, input=data, check=True, capture_output=True
    ).stdout


@pytest.fixture(autouse=True)
def clean_confirmation_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "WAYLAND_DISPLAY",
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SSH_TTY",
        "CANCEL",
        "MUTATE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def wrapper() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("git_ssh_sign", str(WRAPPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def signing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
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


@pytest.mark.parametrize("status", ["10", "20"])
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


@pytest.mark.parametrize("platform", ["linux", "darwin"])
@pytest.mark.parametrize("ssh_variable", ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
def test_ssh_bypasses_inherited_gui(
    wrapper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    platform: str,
    ssh_variable: str,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("WAYLAND_DISPLAY", "inherited-wayland")
    monkeypatch.setenv(ssh_variable, "remote-session")
    gui = mocker.patch.object(wrapper, "gui_confirmation")
    run = mocker.patch.object(wrapper.subprocess, "run")
    terminal = mocker.patch.object(wrapper, "terminal_confirmation", return_value=True)
    assert wrapper.confirm("payload") is True
    terminal.assert_called_once_with("payload")
    gui.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize("approved", [True, False])
def test_no_display_uses_terminal(
    wrapper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    approved: bool,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    gui = mocker.patch.object(wrapper, "gui_confirmation")
    terminal = mocker.patch.object(
        wrapper, "terminal_confirmation", return_value=approved
    )
    assert wrapper.confirm("payload") is approved
    terminal.assert_called_once_with("payload")
    gui.assert_not_called()


@pytest.mark.parametrize("status, approved", [(0, True), (10, False), (20, False)])
def test_wayland_result_does_not_fall_back(
    wrapper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    status: int,
    approved: bool,
) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    monkeypatch.setattr(sys, "platform", "darwin")
    run = mocker.patch.object(
        wrapper.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], status, b"", b""),
    )
    terminal = mocker.patch.object(wrapper, "terminal_confirmation")
    assert wrapper.confirm("payload") is approved
    assert run.call_count == 1
    assert run.call_args.args[0] == [
        "wayprompt",
        "--title",
        "Confirm Git signature",
        "--description",
        "payload",
        "--button-ok",
        "Sign",
        "--button-cancel",
        "Cancel",
    ]
    terminal.assert_not_called()


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("missing"), PermissionError("denied"), 1, 2, -15]
)
@pytest.mark.parametrize("approved", [True, False])
def test_wayland_failure_falls_back(
    wrapper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    failure: OSError | int,
    approved: bool,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    run = mocker.patch.object(wrapper.subprocess, "run")
    if isinstance(failure, OSError):
        run.side_effect = failure
    else:
        run.return_value = subprocess.CompletedProcess(
            [], failure, b"", b"backend error"
        )
    terminal = mocker.patch.object(
        wrapper, "terminal_confirmation", return_value=approved
    )
    assert wrapper.confirm("payload") is approved
    terminal.assert_called_once_with("payload")
    assert "wayprompt" in capsys.readouterr().err


@pytest.mark.parametrize(
    "answer, approved",
    [(b"sign\n", True), (b"cancel\n", False), (b"", False), (b"unexpected\n", False)],
)
def test_macos_confirmation_passes_text_as_data(
    wrapper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    answer: bytes,
    approved: bool,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    text = 'message "quoted" \\ text\non run\ndo shell script "touch /tmp/unwanted"\nend run'
    run = mocker.patch.object(
        wrapper.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, answer, b""),
    )
    terminal = mocker.patch.object(wrapper, "terminal_confirmation")
    assert wrapper.confirm(text) is approved
    args = run.call_args.args[0]
    assert args[:2] == ["osascript", "-e"]
    assert len(args) == 4
    assert args[3] == text
    assert text not in args[2]
    assert "item 1 of argv" in args[2]
    assert 'default button "Sign"' in args[2]
    assert "on error number -128" in args[2]
    assert run.call_args.kwargs == {
        "check": False,
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
    }
    terminal.assert_not_called()


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("missing"), PermissionError("denied"), 1, -15]
)
def test_macos_failure_falls_back(
    wrapper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    failure: OSError | int,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    run = mocker.patch.object(wrapper.subprocess, "run")
    if isinstance(failure, OSError):
        run.side_effect = failure
    else:
        run.return_value = subprocess.CompletedProcess(
            [], failure, b"", b"backend error"
        )
    terminal = mocker.patch.object(wrapper, "terminal_confirmation", return_value=True)
    assert wrapper.confirm("payload") is True
    terminal.assert_called_once_with("payload")
    assert "osascript" in capsys.readouterr().err


def test_macos_after_wayland_failure(
    wrapper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    run = mocker.patch.object(
        wrapper.subprocess,
        "run",
        side_effect=[
            FileNotFoundError("wayprompt missing"),
            subprocess.CompletedProcess([], 0, b"sign\n", b""),
        ],
    )
    terminal = mocker.patch.object(wrapper, "terminal_confirmation")
    assert wrapper.confirm("payload") is True
    assert [call.args[0][0] for call in run.call_args_list] == [
        "wayprompt",
        "osascript",
    ]
    terminal.assert_not_called()


@pytest.mark.parametrize(
    "answers, approved",
    [
        (["Sign\n"], True),
        (["Cancel\n"], False),
        (["cancel\n"], False),
        ([""], False),
        (["\n", "yes\n", "sign\n", "SIGN\n", "Sign\n"], True),
    ],
)
def test_terminal_requires_explicit_approval(
    wrapper: ModuleType,
    mocker: MockerFixture,
    answers: list[str],
    approved: bool,
) -> None:
    tty = mocker.MagicMock(spec=io.TextIOWrapper)
    tty.__enter__.return_value = tty
    tty.isatty.return_value = True
    tty.readline.side_effect = answers
    opened = mocker.patch("builtins.open", return_value=tty)
    assert wrapper.terminal_confirmation("exact description\n") is approved
    assert opened.call_args_list == [
        mocker.call("/dev/tty", encoding="utf-8"),
        mocker.call("/dev/tty", "w", encoding="utf-8"),
    ]
    assert tty.readline.call_count == len(answers)
    assert tty.flush.call_count == len(answers)
    assert tty.write.call_args_list[0].args == (
        "Confirm Git signature\n\nexact description\n\n\n",
    )
    assert tty.__exit__.call_count == 2


@pytest.mark.parametrize("failure", ["open", "not-tty", "read", "write"])
def test_terminal_unavailable_refuses(
    wrapper: ModuleType,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    tty = mocker.MagicMock(spec=io.TextIOWrapper)
    tty.__enter__.return_value = tty
    tty.isatty.return_value = failure != "not-tty"
    opened = mocker.patch("builtins.open", return_value=tty)
    if failure == "open":
        opened.side_effect = OSError("no controlling terminal")
    elif failure == "read":
        tty.readline.side_effect = OSError("terminal disconnected")
    elif failure == "write":
        tty.write.side_effect = OSError("terminal disconnected")
    assert wrapper.terminal_confirmation("payload") is False
    assert "refusing to sign" in capsys.readouterr().err


@pytest.mark.parametrize("approved", [True, False])
def test_terminal_main_preserves_stdin_payload(
    wrapper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    approved: bool,
) -> None:
    payload = b"arbitrary\xff\x00\r\nSign\n\ntrailing spaces  \n\n"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", [str(WRAPPER), "-Y", "sign", "-n", "git", "-"])
    stdin = io.TextIOWrapper(io.BytesIO(payload))
    monkeypatch.setattr(sys, "stdin", stdin)
    mocker.patch.object(wrapper, "description", return_value="payload description")
    tty = mocker.MagicMock(spec=io.TextIOWrapper)
    tty.__enter__.return_value = tty
    tty.isatty.return_value = True
    tty.readline.return_value = "Sign\n" if approved else "Cancel\n"
    mocker.patch("builtins.open", return_value=tty)
    run = mocker.patch.object(
        wrapper.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
    )
    assert wrapper.main() == (0 if approved else 1)
    if approved:
        run.assert_called_once_with(
            ["ssh-keygen", "-Y", "sign", "-n", "git", "-"],
            input=payload,
            check=False,
        )
    else:
        run.assert_not_called()
