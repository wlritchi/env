from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from wlrenv.secwrap import ArgError, Backend, BackendError, parse_args, parse_env_lines


def test_parse_env_lines_empty() -> None:
    assert parse_env_lines("") == {}


def test_parse_env_lines_basic() -> None:
    assert parse_env_lines("FOO=bar\n") == {"FOO": "bar"}


def test_parse_env_lines_multiple() -> None:
    content = "FOO=bar\nBAZ=qux\n"
    assert parse_env_lines(content) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_lines_skips_blank_lines() -> None:
    content = "\nFOO=bar\n\nBAZ=qux\n\n"
    assert parse_env_lines(content) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_lines_skips_comments() -> None:
    content = "# comment\nFOO=bar\n  # indented comment\nBAZ=qux\n"
    assert parse_env_lines(content) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_lines_skips_invalid_keys() -> None:
    # Keys must match [A-Za-z_][A-Za-z0-9_]*
    content = "1FOO=bar\nFO O=bar\n=bar\nFOO=ok\n"
    assert parse_env_lines(content) == {"FOO": "ok"}


def test_parse_env_lines_value_with_equals() -> None:
    # Only the FIRST '=' splits; the rest is literal value.
    content = "TOKEN=abc=def==\n"
    assert parse_env_lines(content) == {"TOKEN": "abc=def=="}


def test_parse_env_lines_value_with_quotes_kept_literal() -> None:
    # Matches bash `export "FOO=\"bar\""`: quotes are literal in the value.
    content = 'FOO="bar"\n'
    assert parse_env_lines(content) == {"FOO": '"bar"'}


def test_parse_env_lines_later_wins() -> None:
    content = "FOO=first\nFOO=second\n"
    assert parse_env_lines(content) == {"FOO": "second"}


def test_parse_env_lines_no_trailing_newline() -> None:
    assert parse_env_lines("FOO=bar") == {"FOO": "bar"}


def test_parse_env_lines_crlf() -> None:
    # Carriage returns are part of the value if present (bash behavior).
    # We don't strip them — keeps parity.
    assert parse_env_lines("FOO=bar\r\n") == {"FOO": "bar\r"}


def test_parse_args_help() -> None:
    args = parse_args(["--help"])
    assert args.help_mode is True
    assert args.list_mode is False
    assert args.from_name is None
    assert args.command is None
    assert args.forwarded == []


def test_parse_args_list() -> None:
    args = parse_args(["--list"])
    assert args.list_mode is True
    assert args.command is None


def test_parse_args_simple_wrap() -> None:
    args = parse_args(["aws", "s3", "ls"])
    assert args.help_mode is False
    assert args.list_mode is False
    assert args.from_name is None
    assert args.command == "aws"
    assert args.forwarded == ["s3", "ls"]


def test_parse_args_from() -> None:
    args = parse_args(["--from", "claude", "node", "script.js"])
    assert args.from_name == "claude"
    assert args.command == "node"
    assert args.forwarded == ["script.js"]


def test_parse_args_from_missing_value() -> None:
    with pytest.raises(ArgError, match="--from requires"):
        parse_args(["--from"])


def test_parse_args_unknown_flag() -> None:
    with pytest.raises(ArgError, match="unknown option"):
        parse_args(["--bogus", "tool"])


def test_parse_args_no_command() -> None:
    # No flags, no command -> Args with command=None
    args = parse_args([])
    assert args.command is None


def test_parse_args_flags_after_command_are_forwarded() -> None:
    # --help after the command name is forwarded, not consumed.
    args = parse_args(["aws", "--help"])
    assert args.help_mode is False
    assert args.command == "aws"
    assert args.forwarded == ["--help"]


def test_parse_args_double_dash_treated_as_command() -> None:
    # `--` is treated as the command name, not as a flag. The bash original
    # rejects it via the `-*) ... unknown option` case; we accept it so the
    # downstream exec produces a clearer "command not found" diagnostic.
    args = parse_args(["--", "echo", "hi"])
    assert args.command == "--"
    assert args.forwarded == ["echo", "hi"]


def test_parse_args_negative_token_is_unknown_flag() -> None:
    # Bash rejects unknown -* tokens before the command.
    with pytest.raises(ArgError, match="unknown option"):
        parse_args(["-x", "tool"])


def test_backend_detect_env_passage(mocker: MockerFixture, tmp_path: Path) -> None:
    store = tmp_path / "passage-store"
    store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(store)},
        clear=True,
    )
    b = Backend.detect()
    assert b.name == "passage"
    assert b.extension == "age"
    assert b.store_dir == store


def test_backend_detect_env_pass(mocker: MockerFixture, tmp_path: Path) -> None:
    store = tmp_path / "pw-store"
    store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "pass", "PASSWORD_STORE_DIR": str(store)},
        clear=True,
    )
    b = Backend.detect()
    assert b.name == "pass"
    assert b.extension == "gpg"
    assert b.store_dir == store


def test_backend_detect_env_unknown_value(mocker: MockerFixture) -> None:
    mocker.patch.dict("os.environ", {"SECWRAP_BACKEND": "weird"}, clear=True)
    with pytest.raises(BackendError, match="SECWRAP_BACKEND"):
        Backend.detect()


def test_backend_detect_autodetect_passage_wins(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    passage_store = tmp_path / "passage-store"
    passage_store.mkdir()
    pass_store = tmp_path / "pw-store"
    pass_store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"PASSAGE_DIR": str(passage_store), "PASSWORD_STORE_DIR": str(pass_store)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
    )
    b = Backend.detect()
    assert b.name == "passage"


def test_backend_detect_autodetect_pass_only(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    pass_store = tmp_path / "pw-store"
    pass_store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"PASSWORD_STORE_DIR": str(pass_store)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: "/usr/bin/pass" if name == "pass" else None,
    )
    b = Backend.detect()
    assert b.name == "pass"


def test_backend_detect_autodetect_no_backend(mocker: MockerFixture) -> None:
    mocker.patch.dict("os.environ", {}, clear=True)
    mocker.patch("shutil.which", return_value=None)
    with pytest.raises(BackendError, match="no usable backend"):
        Backend.detect()


def test_backend_detect_autodetect_binary_present_store_missing(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # passage binary present but its store doesn't exist -> fall through.
    # pass binary present and its store exists -> pass wins.
    pass_store = tmp_path / "pw-store"
    pass_store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"PASSWORD_STORE_DIR": str(pass_store)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
    )
    b = Backend.detect()
    assert b.name == "pass"


def test_backend_show_returns_content(mocker: MockerFixture, tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    backend = Backend(name="pass", binary="pass", extension="gpg", store_dir=store)
    completed = MagicMock(spec=["returncode", "stdout", "stderr"])
    completed.returncode = 0
    completed.stdout = "FOO=bar\n"
    completed.stderr = ""
    run_mock = mocker.patch("subprocess.run", return_value=completed)
    result = backend.show("config/env/aws")
    assert result == "FOO=bar\n"
    run_mock.assert_called_once()
    args, kwargs = run_mock.call_args
    assert args[0][:2] == ["pass", "show"]
    assert args[0][2] == "config/env/aws"


def test_backend_show_returns_none_on_missing(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = Backend(name="pass", binary="pass", extension="gpg", store_dir=tmp_path)
    completed = MagicMock(spec=["returncode", "stdout", "stderr"])
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Error: aws is not in the password store."
    mocker.patch("subprocess.run", return_value=completed)
    assert backend.show("config/env/aws") is None


def test_backend_list_tools_empty(tmp_path: Path) -> None:
    backend = Backend(
        name="passage", binary="passage", extension="age", store_dir=tmp_path
    )
    assert backend.list_tools() == []


def test_backend_list_tools_lists_entries(tmp_path: Path) -> None:
    env_dir = tmp_path / "config" / "env"
    env_dir.mkdir(parents=True)
    (env_dir / "aws.age").write_bytes(b"")
    (env_dir / "claude.age").write_bytes(b"")
    (env_dir / "ignored.gpg").write_bytes(b"")  # wrong extension
    (env_dir / "subdir").mkdir()  # directories are skipped
    backend = Backend(
        name="passage", binary="passage", extension="age", store_dir=tmp_path
    )
    assert backend.list_tools() == ["aws", "claude"]
