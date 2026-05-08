from __future__ import annotations

import pytest

from wlrenv.secwrap import ArgError, parse_args, parse_env_lines


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
