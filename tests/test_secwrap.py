from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from wlrenv.secwrap import (
    ArgError,
    Backend,
    BackendError,
    IncludeError,
    MetaKey,
    MetaKeyError,
    _classify_pubkey,
    _classify_recipients,
    _derive_recipients_from_identities,
    _resolve_inherited_recipients,
    do_bootstrap,
    do_doctor,
    do_rotate_meta,
    format_marker,
    load_meta_key,
    main,
    parse_args,
    parse_env_lines,
    parse_includes,
    parse_marker,
    resolve_includes,
)


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
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
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
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    b = Backend.detect()
    assert b.name == "pass"
    assert b.extension == "gpg"
    assert b.store_dir == store


def test_backend_detect_env_unknown_value(mocker: MockerFixture) -> None:
    mocker.patch.dict("os.environ", {"SECWRAP_BACKEND": "weird"}, clear=True)
    with pytest.raises(BackendError, match="SECWRAP_BACKEND"):
        Backend.detect()


def test_backend_resolve_store_empty_env_falls_back_to_default(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # PASSAGE_DIR="" should be treated as unset and fall back to ~/.passage/store.
    home = tmp_path / "home"
    (home / ".passage" / "store").mkdir(parents=True)
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": "", "HOME": str(home)},
        clear=True,
    )
    mocker.patch("pathlib.Path.home", return_value=home)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    b = Backend.detect()
    assert b.store_dir == home / ".passage" / "store"


def test_backend_detect_empty_secwrap_backend_falls_back_to_autodetect(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    pass_store = tmp_path / "pw-store"
    pass_store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "", "PASSWORD_STORE_DIR": str(pass_store)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: "/usr/bin/pass" if name == "pass" else None,
    )
    b = Backend.detect()
    assert b.name == "pass"


def test_backend_detect_explicit_backend_missing_binary(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    store = tmp_path / "passage-store"
    store.mkdir()
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(store)},
        clear=True,
    )
    mocker.patch("shutil.which", return_value=None)
    with pytest.raises(BackendError, match="passage binary"):
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
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
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
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
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
    args, _kwargs = run_mock.call_args
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


from wlrenv.secwrap import USAGE


def test_main_help_prints_usage_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["--help"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "secwrap" in captured.out
    assert "Usage:" in captured.out


def test_main_list_prints_tools(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    env_dir = tmp_path / "config" / "env"
    env_dir.mkdir(parents=True)
    (env_dir / "aws.age").write_bytes(b"")
    (env_dir / "claude.age").write_bytes(b"")
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    rc = main(["--list"])
    assert rc == 0
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert out_lines == ["aws", "claude"]


def test_main_no_command_prints_usage_to_stderr_and_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict("os.environ", {"SECWRAP_BACKEND": "pass"}, clear=True)
    # Backend.detect needs a real store dir; skip detection by going straight
    # to the "no command" branch via empty argv.
    rc = main([])
    assert rc == 1
    assert "Usage:" in capsys.readouterr().err


def test_main_unknown_flag_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["--bogus"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown option" in err


def test_main_wrap_with_entry_execs_with_merged_env(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    blobs = {
        "config/env-meta": None,
        "config/env/aws": "TOKEN=abc\nREGION=us-east-1\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    main(["aws", "s3", "ls"])

    execvpe.assert_called_once()
    file_arg, argv_arg, env_arg = execvpe.call_args.args
    assert file_arg == "aws"
    assert argv_arg == ["aws", "s3", "ls"]
    assert env_arg["TOKEN"] == "abc"  # noqa: S105
    assert env_arg["REGION"] == "us-east-1"
    assert env_arg["PATH"] == "/usr/bin"  # original env preserved


def test_main_wrap_no_entry_execs_with_unmodified_env(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    completed = MagicMock(spec=["returncode", "stdout", "stderr"])
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Error: aws is not in the password store."
    mocker.patch("subprocess.run", return_value=completed)
    execvpe = mocker.patch("os.execvpe")

    main(["aws", "--version"])

    execvpe.assert_called_once()
    file_arg, argv_arg, env_arg = execvpe.call_args.args
    assert file_arg == "aws"
    assert argv_arg == ["aws", "--version"]
    assert "TOKEN" not in env_arg
    assert env_arg["PATH"] == "/usr/bin"


def test_main_wrap_uses_from_for_lookup_not_command(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    blobs = {
        "config/env-meta": None,
        "config/env/claude": "TOKEN=abc\n",
    }
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    show_mock = mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    main(["--from", "claude", "node", "script.js"])

    # show called with config/env/claude (the --from name), not 'node'.
    fetched = [c.args[0] for c in show_mock.call_args_list]
    assert "config/env/claude" in fetched
    assert "config/env/node" not in fetched
    # exec called with the actual command 'node'.
    file_arg, argv_arg, _ = execvpe.call_args.args
    assert file_arg == "node"
    assert argv_arg == ["node", "script.js"]


def test_usage_constant_mentions_options() -> None:
    assert "--from" in USAGE
    assert "--list" in USAGE
    assert "--help" in USAGE


def test_parse_includes_empty() -> None:
    assert parse_includes("") == []


def test_parse_includes_no_directives() -> None:
    content = "FOO=bar\n# just a comment\nBAR=baz\n"
    assert parse_includes(content) == []


def test_parse_includes_single_directive() -> None:
    content = "# secwrap-include: pnpm\nFOO=bar\n"
    assert parse_includes(content) == ["pnpm"]


def test_parse_includes_multiple_tools_one_line() -> None:
    content = "# secwrap-include: pnpm docker aws\n"
    assert parse_includes(content) == ["pnpm", "docker", "aws"]


def test_parse_includes_multiple_directives_unioned() -> None:
    content = "# secwrap-include: pnpm\nFOO=bar\n# secwrap-include: docker\n"
    assert parse_includes(content) == ["pnpm", "docker"]


def test_parse_includes_leading_whitespace_ok() -> None:
    # Leading whitespace before # is allowed.
    content = "    # secwrap-include: pnpm\n"
    assert parse_includes(content) == ["pnpm"]


def test_parse_includes_drops_invalid_tokens() -> None:
    # "foo bar" splits into "foo" and "bar", both valid. But "f@oo" has an
    # invalid char and is dropped.
    content = "# secwrap-include: foo f@oo bar\n"
    assert parse_includes(content) == ["foo", "bar"]


def test_parse_includes_allows_dot_dash_underscore() -> None:
    content = "# secwrap-include: a.b a-b a_b a1\n"
    assert parse_includes(content) == ["a.b", "a-b", "a_b", "a1"]


def test_parse_includes_ignores_non_directive_comments() -> None:
    content = "# this is just a comment\n# also-secwrap-include: foo\n"
    assert parse_includes(content) == []


def test_parse_includes_preserves_document_order_and_dupes() -> None:
    # Resolver dedupes; parser does not.
    content = "# secwrap-include: a b\n# secwrap-include: b c\n"
    assert parse_includes(content) == ["a", "b", "b", "c"]


def test_parse_marker_empty_string() -> None:
    assert parse_marker("") == set()


def test_parse_marker_single() -> None:
    assert parse_marker("claude") == {"claude"}


def test_parse_marker_multiple() -> None:
    assert parse_marker("claude:docker:pnpm") == {"claude", "docker", "pnpm"}


def test_parse_marker_drops_invalid_tokens() -> None:
    # "bad token" has whitespace -> invalid; ":" with empty segment -> dropped.
    assert parse_marker("claude:bad token::pnpm") == {"claude", "pnpm"}


def test_parse_marker_dedupes() -> None:
    assert parse_marker("a:b:a") == {"a", "b"}


def test_format_marker_empty() -> None:
    assert format_marker([]) == ""


def test_format_marker_alphabetized() -> None:
    assert format_marker(["pnpm", "claude", "docker"]) == "claude:docker:pnpm"


def test_format_marker_dedupes() -> None:
    assert format_marker(["a", "b", "a"]) == "a:b"


def test_format_marker_round_trips_through_parse() -> None:
    names = {"claude", "docker", "pnpm"}
    assert parse_marker(format_marker(names)) == names


def _make_passage_backend(tmp_path: Path) -> Backend:
    return Backend(
        name="passage", binary="passage", extension="age", store_dir=tmp_path
    )


def _make_pass_backend(tmp_path: Path) -> Backend:
    return Backend(name="pass", binary="pass", extension="gpg", store_dir=tmp_path)


def test_resolve_includes_root_missing_returns_empty(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)
    assert resolve_includes(backend, "ghost", marker_loaded=set()) == []


def test_resolve_includes_root_with_no_includes(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value="FOO=bar\n")
    assert resolve_includes(backend, "claude", marker_loaded=set()) == [
        ("claude", "FOO=bar\n"),
    ]


def test_resolve_includes_simple_chain(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/claude": "# secwrap-include: docker\nFOO=claude\n",
        "config/env/docker": "BAR=docker\n",
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    result = resolve_includes(backend, "claude", marker_loaded=set())
    assert result == [
        ("docker", "BAR=docker\n"),
        ("claude", "# secwrap-include: docker\nFOO=claude\n"),
    ]


def test_resolve_includes_diamond_dedupes(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # claude -> {a, b}, a -> shared, b -> shared. shared loaded once.
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/claude": "# secwrap-include: a b\n",
        "config/env/a": "# secwrap-include: shared\n",
        "config/env/b": "# secwrap-include: shared\n",
        "config/env/shared": "X=1\n",
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    result = resolve_includes(backend, "claude", marker_loaded=set())
    names = [n for n, _ in result]
    # `shared` appears once and before its parents; siblings (a, b) in
    # alphabetical order; claude last.
    assert names == ["shared", "a", "b", "claude"]


def test_resolve_includes_siblings_alphabetical(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/root": "# secwrap-include: zebra apple mango\n",
        "config/env/apple": "X=1\n",
        "config/env/mango": "Y=2\n",
        "config/env/zebra": "Z=3\n",
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    result = resolve_includes(backend, "root", marker_loaded=set())
    names = [n for n, _ in result]
    assert names == ["apple", "mango", "zebra", "root"]


def test_resolve_includes_missing_dep_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/claude": "# secwrap-include: ghost\n",
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    with pytest.raises(
        IncludeError, match=r"claude includes 'ghost' but config/env/ghost not found"
    ):
        resolve_includes(backend, "claude", marker_loaded=set())


def test_resolve_includes_direct_cycle_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/a": "# secwrap-include: b\n",
        "config/env/b": "# secwrap-include: a\n",
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    with pytest.raises(IncludeError, match=r"cycle detected: a → b → a"):
        resolve_includes(backend, "a", marker_loaded=set())


def test_resolve_includes_self_cycle_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/a": "# secwrap-include: a\n",
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    with pytest.raises(IncludeError, match=r"cycle detected: a → a"):
        resolve_includes(backend, "a", marker_loaded=set())


def test_resolve_includes_marker_skips_subgraph(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # docker is in marker; we should NOT call show for docker.
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/claude": "# secwrap-include: docker\nFOO=claude\n",
        # config/env/docker would error if fetched — proves it isn't.
    }
    show_mock = mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    result = resolve_includes(backend, "claude", marker_loaded={"docker"})
    assert result == [
        ("docker", None),
        ("claude", "# secwrap-include: docker\nFOO=claude\n"),
    ]
    # show was called only for claude (not docker).
    fetched = [c.args[0] for c in show_mock.call_args_list]
    assert fetched == ["config/env/claude"]


def test_resolve_includes_marker_skips_root(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    show_mock = mocker.patch.object(Backend, "show")
    result = resolve_includes(backend, "claude", marker_loaded={"claude"})
    assert result == [("claude", None)]
    show_mock.assert_not_called()


def test_resolve_includes_pass_walks_chain(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Phase 3: the pass backend now walks the include graph like passage,
    # decrypting each entry via backend.show (pass show). No warning.
    backend = _make_pass_backend(tmp_path)
    blobs = {
        "config/env/claude": "# secwrap-include: docker\nFOO=claude\n",
        "config/env/docker": "BAR=docker\n",
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    result = resolve_includes(backend, "claude", marker_loaded=set())
    assert result == [
        ("docker", "BAR=docker\n"),
        ("claude", "# secwrap-include: docker\nFOO=claude\n"),
    ]
    assert capsys.readouterr().err == ""


def test_resolve_includes_pass_cycle_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    blobs = {
        "config/env/claude": "# secwrap-include: docker\n",
        "config/env/docker": "# secwrap-include: claude\n",
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    with pytest.raises(IncludeError, match=r"cycle detected"):
        resolve_includes(backend, "claude", marker_loaded=set())


def test_resolve_includes_pass_missing_dep_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    blobs = {"config/env/claude": "# secwrap-include: ghost\n"}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    with pytest.raises(
        IncludeError, match=r"claude includes 'ghost' but config/env/ghost not found"
    ):
        resolve_includes(backend, "claude", marker_loaded=set())


def test_resolve_includes_pass_backend_no_includes_no_warning(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value="FOO=bar\n")
    result = resolve_includes(backend, "claude", marker_loaded=set())
    assert result == [("claude", "FOO=bar\n")]
    assert capsys.readouterr().err == ""


def test_main_pass_includes_no_warning(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # End-to-end on the pass backend with no meta key: includes are walked and
    # merged, and no "not yet implemented" warning is emitted.
    blobs = {
        "config/env-meta": None,
        "config/env/claude": "# secwrap-include: docker\nKEY=claude\n",
        "config/env/docker": "DOCKER=yes\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "pass",
            "PASSWORD_STORE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    main(["claude"])

    execvpe.assert_called_once()
    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["KEY"] == "claude"
    assert env_arg["DOCKER"] == "yes"
    assert env_arg["_SECWRAP_LOADED"] == "claude:docker"
    assert "not yet implemented" not in capsys.readouterr().err


def test_main_wrap_short_circuits_when_target_in_marker(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # If pnpm is already in the marker, secwrap should NOT call show
    # (i.e. should not decrypt). Backend.detect() runs (subcommand-dispatch
    # gate needs the backend object), but the marker short-circuit prevents
    # any decryption.
    mocker.patch.dict(
        "os.environ",
        {
            "_SECWRAP_LOADED": "claude:pnpm",
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    show_mock = mocker.patch.object(Backend, "show")
    execvpe = mocker.patch("os.execvpe")

    main(["pnpm", "install"])

    show_mock.assert_not_called()
    execvpe.assert_called_once()
    file_arg, argv_arg, env_arg = execvpe.call_args.args
    assert file_arg == "pnpm"
    assert argv_arg == ["pnpm", "install"]
    # Marker passed through unchanged.
    assert env_arg["_SECWRAP_LOADED"] == "claude:pnpm"


def test_main_wrap_short_circuits_with_from(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # --from rules: marker tracks secret name, so claude in marker means
    # `secwrap --from claude bar` short-circuits without calling show.
    mocker.patch.dict(
        "os.environ",
        {
            "_SECWRAP_LOADED": "claude",
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    show_mock = mocker.patch.object(Backend, "show")
    execvpe = mocker.patch("os.execvpe")

    main(["--from", "claude", "bar"])

    show_mock.assert_not_called()
    execvpe.assert_called_once()
    file_arg, _argv, _env = execvpe.call_args.args
    assert file_arg == "bar"


def test_main_wrap_with_includes_merges_in_topological_order(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # claude includes docker; both define KEY -> claude wins (loaded last).
    blobs = {
        "config/env/claude": "# secwrap-include: docker\nKEY=from_claude\n",
        "config/env/docker": "KEY=from_docker\nDOCKER_ONLY=yes\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    show_mock = mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    main(["claude", "--version"])

    show_mock.assert_called()
    execvpe.assert_called_once()
    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["KEY"] == "from_claude"
    assert env_arg["DOCKER_ONLY"] == "yes"
    # Marker is set to alphabetized union.
    assert env_arg["_SECWRAP_LOADED"] == "claude:docker"


def test_main_wrap_marker_union_with_existing(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    blobs = {
        "config/env/claude": "FOO=bar\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "_SECWRAP_LOADED": "existing",
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    main(["claude"])

    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["_SECWRAP_LOADED"] == "claude:existing"


def test_main_wrap_no_entry_does_not_set_marker(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # No config/env/aws entry. We should still exec, with no env mutation
    # AND no marker update (resolver returned []).
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", return_value=None)
    execvpe = mocker.patch("os.execvpe")

    main(["aws", "--version"])

    _file, _argv, env_arg = execvpe.call_args.args
    assert "_SECWRAP_LOADED" not in env_arg


def test_main_wrap_missing_include_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    blobs = {
        "config/env/claude": "# secwrap-include: ghost\n",
    }
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    rc = main(["claude"])
    assert rc == 1
    execvpe.assert_not_called()
    err = capsys.readouterr().err
    assert "claude includes 'ghost'" in err


def test_main_wrap_cycle_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    blobs = {
        "config/env/a": "# secwrap-include: b\n",
        "config/env/b": "# secwrap-include: a\n",
    }
    mocker.patch.dict(
        "os.environ",
        {"SECWRAP_BACKEND": "passage", "PASSAGE_DIR": str(tmp_path)},
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))

    rc = main(["a"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "cycle detected" in err


def test_main_wrap_marker_skip_does_not_re_decrypt(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # claude includes docker; docker is already in marker. show should be
    # called for claude only (plus the meta key probe).
    blobs = {
        "config/env-meta": None,
        "config/env/claude": "# secwrap-include: docker\nFOO=bar\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "_SECWRAP_LOADED": "docker",
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    show_mock = mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    main(["claude"])

    fetched = [c.args[0] for c in show_mock.call_args_list]
    assert "config/env/claude" in fetched
    assert "config/env/docker" not in fetched
    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["_SECWRAP_LOADED"] == "claude:docker"
    assert env_arg["FOO"] == "bar"


def test_parse_args_double_dash_sets_force_wrap_and_consumes() -> None:
    args = parse_args(["--", "bootstrap", "arg1"])
    assert args.force_wrap is True
    assert args.command == "bootstrap"
    assert args.forwarded == ["arg1"]


def test_parse_args_double_dash_after_from() -> None:
    args = parse_args(["--from", "claude", "--", "doctor"])
    assert args.force_wrap is True
    assert args.from_name == "claude"
    assert args.command == "doctor"


def test_parse_args_double_dash_alone_is_error() -> None:
    # `secwrap --` with no command after: USAGE error path (no command).
    args = parse_args(["--"])
    assert args.force_wrap is True
    assert args.command is None


def test_parse_args_no_double_dash_force_wrap_false() -> None:
    args = parse_args(["claude"])
    assert args.force_wrap is False
    assert args.command == "claude"


def test_load_meta_key_missing_returns_none(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)
    assert load_meta_key(backend) is None


def test_load_meta_key_valid_json_age(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = _make_passage_backend(tmp_path)
    blob = '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}\n'
    mocker.patch.object(Backend, "show", return_value=blob)
    mk = load_meta_key(backend)
    assert mk is not None
    assert mk.backend == "age"
    assert mk.key == b"AGE-SECRET-KEY-1FAKE"


def test_load_meta_key_invalid_json_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value="not json")
    with pytest.raises(MetaKeyError, match=r"config/env-meta is not valid JSON"):
        load_meta_key(backend)


def test_load_meta_key_backend_mismatch_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blob = '{"backend": "gpg", "key": "...", "passphrase": "..."}'
    mocker.patch.object(Backend, "show", return_value=blob)
    with pytest.raises(
        MetaKeyError, match=r"declares backend=gpg but detected backend is passage"
    ):
        load_meta_key(backend)


def test_load_meta_key_missing_required_field_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blob = '{"backend": "age"}'  # no "key"
    mocker.patch.object(Backend, "show", return_value=blob)
    with pytest.raises(MetaKeyError, match=r"missing required field 'key'"):
        load_meta_key(backend)


def test_meta_key_decrypt_invokes_age(mocker: MockerFixture, tmp_path: Path) -> None:
    mk = MetaKey(backend="age", key=b"AGE-SECRET-KEY-1FAKE")
    run_mock = mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"FOO=bar\n", stderr=b""
        ),
    )
    result = mk.decrypt(tmp_path, "claude", "age")
    assert result == "FOO=bar\n"
    run_mock.assert_called_once()
    call = run_mock.call_args
    assert call.args[0][:3] == ["age", "-d", "--identity"]
    assert call.kwargs["input"] == b"AGE-SECRET-KEY-1FAKE"
    # Path arg should be the entry's full path:
    assert call.args[0][-1] == str(tmp_path / "config/env/claude.age")


def test_meta_key_decrypt_failure_raises(mocker: MockerFixture, tmp_path: Path) -> None:
    mk = MetaKey(backend="age", key=b"AGE-SECRET-KEY-1FAKE")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"age: bad key\n"
        ),
    )
    with pytest.raises(MetaKeyError, match=r"age decryption failed"):
        mk.decrypt(tmp_path, "claude", "age")


def test_load_meta_key_valid_json_gpg(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = _make_pass_backend(tmp_path)
    blob = '{"backend": "gpg", "passphrase": "cGFzcw==", "key": "PGP-ARMOR"}\n'
    mocker.patch.object(Backend, "show", return_value=blob)
    mk = load_meta_key(backend)
    assert mk is not None
    assert mk.backend == "gpg"
    assert mk.key == b"PGP-ARMOR"
    assert mk.passphrase == b"cGFzcw=="


def test_load_meta_key_gpg_missing_passphrase_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    blob = '{"backend": "gpg", "key": "PGP-ARMOR"}'  # no passphrase
    mocker.patch.object(Backend, "show", return_value=blob)
    with pytest.raises(MetaKeyError, match=r"missing required field 'passphrase'"):
        load_meta_key(backend)


def test_meta_key_gpg_decrypt_imports_once_and_decrypts(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    home = tmp_path / "gpghome"
    home.mkdir()
    mocker.patch("tempfile.mkdtemp", return_value=str(home))
    mk = MetaKey(
        backend="gpg",
        key=b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n",
        passphrase=b"secretpw",
    )
    calls: list[tuple[list[str], object]] = []

    def fake_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((cmd, kwargs.get("input")))
        stdout = b"FOO=bar\n" if "--decrypt" in cmd else b""
        return subprocess.CompletedProcess(cmd, 0, stdout, b"")

    mocker.patch("subprocess.run", side_effect=fake_run)

    r1 = mk.decrypt(tmp_path, "claude", "gpg")
    r2 = mk.decrypt(tmp_path, "docker", "gpg")

    assert r1 == "FOO=bar\n"
    assert r2 == "FOO=bar\n"
    imports = [(c, i) for c, i in calls if "--import" in c]
    decrypts = [(c, i) for c, i in calls if "--decrypt" in c]
    # Key imported into the temp homedir exactly once, reused for both entries.
    assert len(imports) == 1
    assert imports[0][1] == b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
    assert "--homedir" in imports[0][0]
    assert str(home) in imports[0][0]
    assert len(decrypts) == 2
    dcmd, dinput = decrypts[0]
    assert "--pinentry-mode" in dcmd
    assert "loopback" in dcmd
    assert dinput == b"secretpw"  # passphrase piped on stdin, never on argv
    assert "secretpw" not in " ".join(dcmd)
    assert dcmd[-1] == str(tmp_path / "config/env/claude.gpg")


def test_meta_key_gpg_cleanup_kills_agent_and_removes_home(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    home = tmp_path / "gpghome"
    home.mkdir()
    mocker.patch("tempfile.mkdtemp", return_value=str(home))
    killed: list[list[str]] = []

    def fake_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        if cmd and cmd[0] == "gpgconf":
            killed.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"FOO=bar\n", b"")

    mocker.patch("subprocess.run", side_effect=fake_run)
    mk = MetaKey(backend="gpg", key=b"KEY", passphrase=b"pw")
    mk.decrypt(tmp_path, "claude", "gpg")
    assert home.exists()

    mk.cleanup()

    assert any("gpg-agent" in c for c in killed)
    assert not home.exists()
    # cleanup is idempotent
    mk.cleanup()


def test_meta_key_gpg_decrypt_failure_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    home = tmp_path / "gpghome"
    home.mkdir()
    mocker.patch("tempfile.mkdtemp", return_value=str(home))
    mk = MetaKey(backend="gpg", key=b"KEY", passphrase=b"pw")

    def fake_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        rc = 1 if "--decrypt" in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, b"", b"gpg: decryption failed\n")

    mocker.patch("subprocess.run", side_effect=fake_run)
    with pytest.raises(MetaKeyError, match=r"gpg decryption failed"):
        mk.decrypt(tmp_path, "claude", "gpg")


def test_main_pass_uses_gpg_meta_key(mocker: MockerFixture, tmp_path: Path) -> None:
    blobs = {
        "config/env-meta": '{"backend": "gpg", "passphrase": "cHc=", "key": "ARMOR"}',
    }
    decrypt_blobs = {
        "claude": "# secwrap-include: docker\nKEY=claude\n",
        "docker": "DOCKER=yes\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "pass",
            "PASSWORD_STORE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypt_blobs[name]
    )
    cleanup = mocker.patch.object(MetaKey, "cleanup")
    execvpe = mocker.patch("os.execvpe")

    main(["claude"])

    execvpe.assert_called_once()
    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["KEY"] == "claude"
    assert env_arg["DOCKER"] == "yes"
    assert env_arg["_SECWRAP_LOADED"] == "claude:docker"
    # The gpg temp GNUPGHOME is torn down before exec.
    cleanup.assert_called()


def test_resolve_includes_uses_meta_key_when_provided(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mk = MetaKey(backend="age", key=b"FAKEKEY")
    blobs = {
        "claude": "# secwrap-include: docker\nFOO=claude\n",
        "docker": "BAR=docker\n",
    }
    decrypt_mock = mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: blobs[name]
    )
    show_mock = mocker.patch.object(Backend, "show")

    result = resolve_includes(backend, "claude", marker_loaded=set(), meta_key=mk)

    # Should have used decrypt, not show.
    assert decrypt_mock.call_count == 2
    show_mock.assert_not_called()
    names = [n for n, _ in result]
    assert names == ["docker", "claude"]


def test_resolve_includes_meta_key_none_falls_back_to_show(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # meta_key=None: behavior identical to Phase 2a.
    backend = _make_passage_backend(tmp_path)
    blobs = {"config/env/claude": "FOO=bar\n"}
    show_mock = mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))

    result = resolve_includes(backend, "claude", marker_loaded=set(), meta_key=None)

    show_mock.assert_called_once_with("config/env/claude")
    assert result == [("claude", "FOO=bar\n")]


def test_main_loads_meta_key_and_uses_it(mocker: MockerFixture, tmp_path: Path) -> None:
    blobs = {
        "config/env-meta": '{"backend": "age", "key": "FAKE"}',
    }
    decrypt_blobs = {
        "claude": "# secwrap-include: docker\nKEY=claude\n",
        "docker": "DOCKER=yes\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypt_blobs[name]
    )
    execvpe = mocker.patch("os.execvpe")

    main(["claude"])

    execvpe.assert_called_once()
    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["KEY"] == "claude"
    assert env_arg["DOCKER"] == "yes"
    assert env_arg["_SECWRAP_LOADED"] == "claude:docker"


def test_main_warns_when_meta_absent_and_includes_present(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # meta entry missing, but claude has includes -> warn.
    blobs = {
        "config/env-meta": None,
        "config/env/claude": "# secwrap-include: docker\nKEY=claude\n",
        "config/env/docker": "DOCKER=yes\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("os.execvpe")

    main(["claude"])

    err = capsys.readouterr().err
    assert "meta key absent" in err
    assert "2 includes" in err  # claude + docker = 2 entries
    assert "secwrap bootstrap" in err


def test_main_no_warn_for_single_entry_no_meta(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Single entry, no includes — meta absent shouldn't warn.
    blobs = {
        "config/env-meta": None,
        "config/env/claude": "FOO=bar\n",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("os.execvpe")

    main(["claude"])

    assert capsys.readouterr().err == ""


def test_main_meta_key_error_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Malformed meta entry: hard error, exit 1, don't exec.
    blobs = {
        "config/env-meta": "not json",
    }
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    execvpe = mocker.patch("os.execvpe")

    rc = main(["claude"])

    assert rc == 1
    execvpe.assert_not_called()
    err = capsys.readouterr().err
    assert "config/env-meta is not valid JSON" in err


def test_main_bootstrap_dispatches(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    do_bootstrap = mocker.patch("wlrenv.secwrap.do_bootstrap", return_value=0)

    rc = main(["bootstrap"])

    assert rc == 0
    do_bootstrap.assert_called_once()


def test_main_rotate_meta_dispatches(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    do_rotate = mocker.patch("wlrenv.secwrap.do_rotate_meta", return_value=0)

    rc = main(["rotate-meta", "--yes"])

    assert rc == 0
    do_rotate.assert_called_once()
    # Args struct should preserve the --yes
    call_args = do_rotate.call_args
    # First positional is backend; second is args.forwarded (or similar)
    assert "--yes" in call_args.args[1] or call_args.args[1] == ["--yes"]


def test_main_doctor_dispatches(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    do_doctor = mocker.patch("wlrenv.secwrap.do_doctor", return_value=0)

    rc = main(["doctor"])

    assert rc == 0
    do_doctor.assert_called_once()


def test_main_subcommand_pass_backend_dispatches(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # Phase 3: subcommands are supported on pass; main dispatches to the handler.
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "pass",
            "PASSWORD_STORE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    do_bootstrap = mocker.patch("wlrenv.secwrap.do_bootstrap", return_value=0)

    rc = main(["bootstrap"])

    assert rc == 0
    do_bootstrap.assert_called_once()
    # The pass backend object is handed to the handler.
    assert do_bootstrap.call_args.args[0].name == "pass"


def test_main_force_wrap_skips_subcommand(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # `secwrap -- bootstrap` wraps a binary called bootstrap, doesn't dispatch.
    mocker.patch.dict(
        "os.environ",
        {
            "SECWRAP_BACKEND": "passage",
            "PASSAGE_DIR": str(tmp_path),
            "PATH": "/usr/bin",
        },
        clear=True,
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"pass", "passage"} else None
        ),
    )
    mocker.patch.object(Backend, "show", return_value=None)  # no meta, no entry
    do_bootstrap = mocker.patch("wlrenv.secwrap.do_bootstrap")
    execvpe = mocker.patch("os.execvpe")

    main(["--", "bootstrap", "arg"])

    do_bootstrap.assert_not_called()
    execvpe.assert_called_once()
    file_arg, argv_arg, _ = execvpe.call_args.args
    assert file_arg == "bootstrap"
    assert argv_arg == ["bootstrap", "arg"]


def test_do_bootstrap_happy_path(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = _make_passage_backend(tmp_path)
    # Simulate passage store layout: store_dir/config/env/.age-recipients
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients_file = tmp_path / "config" / "env" / ".age-recipients"
    recipients_file.write_text("age1user...\n")

    mocker.patch.object(Backend, "show", return_value=None)  # no existing meta
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    # Mock subprocess.run for each shell-out.
    insert_inputs: list[str | None] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            raise AssertionError(
                "bootstrap should NOT request -pq when recipients are classic"
            )
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pub...\n", "")
        if cmd[0] == "age-keygen":
            # Classic key emitted on stdout (no -o flag).
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-1FAKE\n", "")
        if cmd[0] == "passage" and cmd[1] == "reencrypt":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "passage" and cmd[1] == "insert":
            insert_inputs.append(kwargs.get("input"))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 0
    contents = recipients_file.read_text().splitlines()
    assert "age1user..." in contents
    assert "age1pub..." in contents

    # passage insert stdin payload is valid JSON with the right schema.
    assert insert_inputs, "passage insert should have been invoked"
    payload_str = insert_inputs[0]
    assert isinstance(payload_str, str)
    payload = json.loads(payload_str)
    assert isinstance(payload, dict)
    assert payload["backend"] == "age"
    assert payload["key"] == "AGE-SECRET-KEY-1FAKE"


def _fake_gpg_gen_run(
    captured: dict[str, Any],
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a subprocess.run fake covering the pass-bootstrap gpg/pass shell-outs."""

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "gpg" and "--quick-generate-key" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "gpg" and "--list-secret-keys" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "fpr:::::::::METAFPR123:\n", "")
        if cmd[0] == "gpg" and "--export-secret-keys" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, "-----BEGIN PGP PRIVATE KEY BLOCK-----\nZ\n", ""
            )
        if cmd[0] == "gpg" and "--export" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, "-----BEGIN PGP PUBLIC KEY BLOCK-----\nZ\n", ""
            )
        if cmd[0] == "gpg" and "--import-ownertrust" in cmd:
            captured["ownertrust_input"] = kwargs.get("input")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "gpg" and "--import" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "gpgconf":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "pass" and cmd[1] == "init":
            captured["init_cmd"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "pass" and cmd[1] == "insert":
            captured.setdefault("insert_inputs", []).append(kwargs.get("input"))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    return fake_run


def test_do_bootstrap_pass_happy_path(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = _make_pass_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    # store-root .gpg-id: the user's key, inherited by config/env/.
    (tmp_path / ".gpg-id").write_text("USERKEYID\n")
    gen_home = tmp_path / "gen"
    gen_home.mkdir()
    mocker.patch("tempfile.mkdtemp", return_value=str(gen_home))
    mocker.patch.object(Backend, "show", return_value=None)  # no existing meta
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"gpg", "pass"} else None
        ),
    )
    captured: dict[str, Any] = {}
    mocker.patch("subprocess.run", side_effect=_fake_gpg_gen_run(captured))

    rc = do_bootstrap(backend, [])

    assert rc == 0
    # pass init re-encrypts config/env to user key + meta fingerprint.
    init_cmd = captured["init_cmd"]
    assert isinstance(init_cmd, list)
    assert init_cmd[:4] == ["pass", "init", "-p", "config/env"]
    assert "USERKEYID" in init_cmd
    assert "METAFPR123" in init_cmd
    # Meta key marked ultimately trusted so pass can encrypt to it.
    assert captured["ownertrust_input"] == "METAFPR123:6:\n"
    # Meta payload is valid gpg-schema JSON.
    insert_inputs = captured["insert_inputs"]
    assert isinstance(insert_inputs, list)
    payload = json.loads(insert_inputs[0])
    assert payload["backend"] == "gpg"
    assert payload["key"].startswith("-----BEGIN PGP PRIVATE KEY BLOCK-----")
    assert payload["passphrase"]  # non-empty base64 passphrase
    # The throwaway generation homedir is removed.
    assert not gen_home.exists()


def test_do_bootstrap_pass_meta_already_exists(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "gpg", "passphrase": "p", "key": "k"}',
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"gpg", "pass"} else None
        ),
    )
    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "config/env-meta already exists" in err
    assert "rotate-meta" in err


def test_do_bootstrap_pass_gpg_missing(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch("shutil.which", return_value=None)
    rc = do_bootstrap(backend, [])
    assert rc == 1
    assert "gpg not found" in capsys.readouterr().err


def test_do_bootstrap_pass_no_gpg_id(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"gpg", "pass"} else None
        ),
    )
    rc = do_bootstrap(backend, [])
    assert rc == 1
    assert "cannot determine existing gpg-id" in capsys.readouterr().err


def test_do_bootstrap_pass_init_failure_aborts_and_cleans_up(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    (tmp_path / ".gpg-id").write_text("USERKEYID\n")
    gen_home = tmp_path / "gen"
    gen_home.mkdir()
    mocker.patch("tempfile.mkdtemp", return_value=str(gen_home))
    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"gpg", "pass"} else None
        ),
    )
    captured: dict[str, Any] = {}
    base = _fake_gpg_gen_run(captured)

    def fail_init(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "pass" and cmd[1] == "init":
            return subprocess.CompletedProcess(cmd, 1, "", "pass: init error\n")
        return base(cmd, **kwargs)

    mocker.patch("subprocess.run", side_effect=fail_init)

    rc = do_bootstrap(backend, [])
    assert rc == 1
    assert "pass init config/env failed" in capsys.readouterr().err
    # Generation homedir still cleaned up despite the failure.
    assert not gen_home.exists()


def test_do_bootstrap_meta_already_exists(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(
        Backend, "show", return_value='{"backend": "age", "key": "..."}'
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )
    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "config/env-meta already exists" in err
    assert "rotate-meta" in err


def test_do_bootstrap_age_keygen_missing(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch("shutil.which", return_value=None)  # nothing on PATH
    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "age-keygen not found" in err


def test_do_bootstrap_errors_when_pq_needed_but_unsupported(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Existing recipients are PQ, so meta MUST be PQ. If age-keygen lacks
    # `-pq`, bootstrap must error rather than silently producing a classic
    # meta (which age would then refuse to mix with the PQ recipients).
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1pq1userpq\n")

    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "unrecognized flag: -pq\n")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pq1metapub\n", "")
        if cmd[0] == "age-keygen":
            raise AssertionError(
                "bootstrap should NOT fall back to classic when -pq is required"
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "age-keygen -pq" in err


def test_do_bootstrap_reencrypt_failure_aborts(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients_file = tmp_path / "config" / "env" / ".age-recipients"
    recipients_file.write_text("age1user\n")

    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-1FAKE\n", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pub\n", "")
        if cmd[0] == "passage" and cmd[1] == "reencrypt":
            return subprocess.CompletedProcess(cmd, 1, "", "passage: error\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "passage reencrypt failed" in err


def test_do_rotate_meta_without_yes_describes(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "age", "key": "AGE-SECRET-KEY-1OLD"}',
    )

    rc = do_rotate_meta(backend, [])

    assert rc == 0
    out = capsys.readouterr().out
    # Description goes to stdout
    assert "rotate-meta will" in out.lower() or "this will" in out.lower()
    assert "--yes" in out


def test_do_rotate_meta_with_yes_happy_path(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients = tmp_path / "config" / "env" / ".age-recipients"
    recipients.write_text("age1user\nage1oldmeta\n")

    # Existing meta with old pubkey embedded — the rotate path needs to know
    # which recipient to remove. We derive it from age-keygen -y on the OLD
    # key, which we'll mock.
    old_blob = '{"backend": "age", "key": "AGE-SECRET-KEY-1OLD"}'
    mocker.patch.object(Backend, "show", return_value=old_blob)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    insert_calls: list[tuple[list[str], str | None]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            raise AssertionError(
                "rotate-meta should NOT request -pq when residue is classic"
            )
        if cmd[0] == "age-keygen" and "-y" in cmd:
            # Distinguish OLD vs NEW based on the key piped in via stdin.
            stdin_text = kwargs.get("input") or ""
            assert isinstance(stdin_text, str)
            if "OLD" in stdin_text:
                return subprocess.CompletedProcess(cmd, 0, "age1oldmeta\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1newmeta\n", "")
        if cmd[0] == "age-keygen":
            # Classic new key on stdout.
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-1NEW\n", "")
        if cmd[0] == "passage" and cmd[1] == "insert":
            insert_calls.append((cmd, kwargs.get("input")))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_rotate_meta(backend, ["--yes"])

    assert rc == 0
    contents = recipients.read_text().splitlines()
    assert "age1user" in contents
    assert "age1newmeta" in contents
    assert "age1oldmeta" not in contents

    # passage insert was called with --force and a valid JSON payload.
    assert insert_calls, "passage insert should have been invoked"
    insert_cmd, insert_stdin = insert_calls[0]
    assert "--force" in insert_cmd
    assert isinstance(insert_stdin, str)
    payload = json.loads(insert_stdin)
    assert isinstance(payload, dict)
    assert payload["backend"] == "age"
    assert payload["key"] == "AGE-SECRET-KEY-1NEW"


def test_do_rotate_meta_no_existing_meta(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)

    rc = do_rotate_meta(backend, ["--yes"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no config/env-meta found" in err
    assert "bootstrap" in err


def test_do_doctor_all_clean(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients = tmp_path / "config" / "env" / ".age-recipients"
    recipients.write_text("age1user\nage1meta\n")

    # Two entries.
    (tmp_path / "config" / "env" / "claude.age").write_bytes(b"fake-encrypted")
    (tmp_path / "config" / "env" / "docker.age").write_bytes(b"fake-encrypted")

    # show: meta entry; list_tools: claude, docker.
    blobs = {
        "config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}',
    }
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    decrypt_blobs = {
        "claude": "# secwrap-include: docker\nFOO=claude\n",
        "docker": "BAR=docker\n",
    }
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypt_blobs[name]
    )
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1meta\n", ""),
    )

    rc = do_doctor(backend, [])

    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out or "✓" in out  # some indicator of pass


def test_do_doctor_missing_meta(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value=None)
    rc = do_doctor(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "config/env-meta" in err


def test_do_doctor_recipient_drift(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    # Recipient list MISSING the meta pubkey.
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1user\n")

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")
    # age-keygen -y returns a pubkey not in the recipients file.
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1meta-missing\n", ""),
    )

    rc = do_doctor(backend, [])

    assert rc == 1
    err = capsys.readouterr().err
    assert "recipient" in err.lower()
    assert "age1meta-missing" in err


def test_do_doctor_entry_decrypt_failure(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1user\nage1meta\n")
    (tmp_path / "config" / "env" / "broken.age").write_bytes(b"fake")

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1meta\n", ""),
    )
    # Decrypt raises for the broken entry.
    mocker.patch.object(
        MetaKey,
        "decrypt",
        side_effect=MetaKeyError("age decryption failed for broken: bad MAC"),
    )

    rc = do_doctor(backend, [])

    assert rc == 1
    err = capsys.readouterr().err
    assert "broken" in err
    assert "decrypt" in err.lower()


def test_do_doctor_cycle_detected(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1user\nage1meta\n")
    (tmp_path / "config" / "env" / "a.age").write_bytes(b"fake")
    (tmp_path / "config" / "env" / "b.age").write_bytes(b"fake")

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1meta\n", ""),
    )
    decrypt_blobs = {
        "a": "# secwrap-include: b\n",
        "b": "# secwrap-include: a\n",
    }
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypt_blobs[name]
    )

    rc = do_doctor(backend, [])

    assert rc == 1
    err = capsys.readouterr().err
    assert "cycle" in err.lower()


def test_derive_recipients_from_identities_plain_key(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    identities = tmp_path / "identities"
    identities.write_text("AGE-SECRET-KEY-1FAKE\n")
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd[:2] == ["age-keygen", "-y"]
        assert kwargs.get("input") == "AGE-SECRET-KEY-1FAKE"
        return subprocess.CompletedProcess(cmd, 0, "age1userpub\n", "")

    mocker.patch("subprocess.run", side_effect=fake_run)
    assert _derive_recipients_from_identities(identities) == ["age1userpub"]


def test_derive_recipients_from_identities_parses_plugin_comment(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # Plugin identities (e.g. yubikey) carry the pubkey as a "# public key:"
    # comment. age-keygen -y can't process the plugin line itself.
    identities = tmp_path / "identities"
    identities.write_text("# public key: age1yubipub\nAGE-PLUGIN-YUBIKEY-1ABCDEF\n")
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")
    # age-keygen returns no stdout (no plain key lines to convert).
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "", ""),
    )
    assert _derive_recipients_from_identities(identities) == ["age1yubipub"]


def test_derive_recipients_from_identities_mixed(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    identities = tmp_path / "identities"
    identities.write_text(
        "# public key: age1yubipub\nAGE-PLUGIN-YUBIKEY-1XYZ\nAGE-SECRET-KEY-1PLAIN\n"
    )
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        # We expect only the plain key to be piped to age-keygen.
        assert kwargs.get("input") == "AGE-SECRET-KEY-1PLAIN"
        return subprocess.CompletedProcess(cmd, 0, "age1plainpub\n", "")

    mocker.patch("subprocess.run", side_effect=fake_run)
    assert _derive_recipients_from_identities(identities) == [
        "age1yubipub",
        "age1plainpub",
    ]


def test_derive_recipients_from_identities_missing_file(tmp_path: Path) -> None:
    assert _derive_recipients_from_identities(tmp_path / "nope") == []


def test_resolve_inherited_recipients_walks_up(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """When `<store>/.age-recipients` exists, use its contents — passage's
    walk-up resolution."""
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / ".age-recipients").write_text("age1walkedup\nage1another\n")
    mocker.patch.dict("os.environ", {}, clear=True)

    assert _resolve_inherited_recipients(tmp_path) == [
        "age1walkedup",
        "age1another",
    ]


def test_resolve_inherited_recipients_prefers_closer_dir(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """`<store>/config/.age-recipients` wins over `<store>/.age-recipients`
    because it's closer to `config/env/`."""
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / ".age-recipients").write_text("age1root\n")
    (tmp_path / "config" / ".age-recipients").write_text("age1config\n")
    mocker.patch.dict("os.environ", {}, clear=True)

    assert _resolve_inherited_recipients(tmp_path) == ["age1config"]


def test_resolve_inherited_recipients_falls_back_to_identities(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """No .age-recipients anywhere → derive from $PASSAGE_IDENTITIES_FILE.
    This is the affected real-world case (passage with no recipients file)."""
    identities = tmp_path / "identities"
    identities.write_text("AGE-SECRET-KEY-1USER\n")
    mocker.patch.dict(
        "os.environ", {"PASSAGE_IDENTITIES_FILE": str(identities)}, clear=True
    )
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "age1userpub\n", ""),
    )

    assert _resolve_inherited_recipients(tmp_path) == ["age1userpub"]


def test_resolve_inherited_recipients_honors_PASSAGE_RECIPIENTS_FILE(  # noqa: N802
    mocker: MockerFixture, tmp_path: Path
) -> None:
    overrides = tmp_path / "custom-recipients"
    overrides.write_text("age1override\n")
    # Walked-up file also exists, to verify the env var takes precedence.
    (tmp_path / ".age-recipients").write_text("age1notthis\n")
    mocker.patch.dict(
        "os.environ", {"PASSAGE_RECIPIENTS_FILE": str(overrides)}, clear=True
    )

    assert _resolve_inherited_recipients(tmp_path) == ["age1override"]


def test_resolve_inherited_recipients_honors_PASSAGE_RECIPIENTS(  # noqa: N802
    mocker: MockerFixture, tmp_path: Path
) -> None:
    (tmp_path / ".age-recipients").write_text("age1notthis\n")
    mocker.patch.dict(
        "os.environ",
        {"PASSAGE_RECIPIENTS": "age1one age1two"},
        clear=True,
    )

    assert _resolve_inherited_recipients(tmp_path) == ["age1one", "age1two"]


def test_do_bootstrap_derives_from_identities_when_no_recipients_file(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """The reported real-world bug case: the store has no `.age-recipients`
    file anywhere; passage was deriving from `~/.passage/identities`. Bootstrap
    must capture the identity-derived pubkey in the new local file or the
    user's main key loses decryption access."""
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    identities = tmp_path / "identities"
    identities.write_text("AGE-SECRET-KEY-1USER\n")
    mocker.patch.dict("os.environ", {"PASSAGE_IDENTITIES_FILE": str(identities)})
    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            raise AssertionError(
                "bootstrap should NOT request -pq when identity is classic"
            )
        if cmd[0] == "age-keygen" and "-y" in cmd:
            piped = kwargs.get("input") or ""
            assert isinstance(piped, str)
            # When deriving inherited recipients, age-keygen -y is invoked on
            # the user identity. When deriving the new meta pubkey, it's
            # invoked on the freshly-generated meta key.
            if "USER" in piped:
                return subprocess.CompletedProcess(cmd, 0, "age1userpub\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1metapub\n", "")
        if cmd[0] == "age-keygen":
            # Classic meta key on stdout.
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-1NEW\n", "")
        if cmd[0] == "passage":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 0

    local = (tmp_path / "config" / "env" / ".age-recipients").read_text().splitlines()
    assert "age1userpub" in local
    assert "age1metapub" in local


def test_do_bootstrap_aborts_when_no_recipient_source(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """If neither inherited `.age-recipients` nor identities can yield any
    recipient, bootstrap MUST refuse rather than silently writing a file
    that drops the user's decryption access."""
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    # No identities file at the default path either.
    mocker.patch.dict(
        "os.environ",
        {"PASSAGE_IDENTITIES_FILE": str(tmp_path / "nonexistent")},
    )
    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "cannot determine existing recipients" in err


def test_do_doctor_detects_missing_inherited_recipient(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The buggy-bootstrap state: local file has only the meta pubkey; the
    user's main identity (the inherited recipient) is missing."""
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    identities = tmp_path / "identities"
    identities.write_text("AGE-SECRET-KEY-1USER\n")
    # Local recipients file has ONLY the meta pubkey — the broken state.
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1meta\n")
    mocker.patch.dict("os.environ", {"PASSAGE_IDENTITIES_FILE": str(identities)})

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["age-keygen", "-y"]:
            piped = kwargs.get("input") or ""
            assert isinstance(piped, str)
            if "USER" in piped:
                return subprocess.CompletedProcess(cmd, 0, "age1userpub\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1meta\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_doctor(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "inherited recipient" in err
    assert "age1userpub" in err
    assert "--fix" in err


def test_do_doctor_fix_repairs_missing_recipients(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`doctor --fix` adds the missing inherited recipient(s) and re-encrypts
    each entry so the user's main key regains decryption access."""
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    identities = tmp_path / "identities"
    identities.write_text("AGE-SECRET-KEY-1USER\n")
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1meta\n")
    (tmp_path / "config" / "env" / "claude.age").write_bytes(b"original-ciphertext")
    (tmp_path / "config" / "env" / "docker.age").write_bytes(b"original-ciphertext")
    mocker.patch.dict("os.environ", {"PASSAGE_IDENTITIES_FILE": str(identities)})

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("shutil.which", return_value="/usr/bin/age")

    decrypted = {"claude": "FOO=claude\n", "docker": "BAR=docker\n"}
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypted[name]
    )

    age_invocations: list[tuple[list[str], bytes | None]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["age-keygen", "-y"]:
            piped = kwargs.get("input") or ""
            assert isinstance(piped, str)
            if "USER" in piped:
                return subprocess.CompletedProcess(cmd, 0, "age1userpub\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1meta\n", "")
        if cmd[0] == "age" and "-e" in cmd:
            age_invocations.append((cmd, kwargs.get("input")))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            # Honor the -o argument so .replace() finds the tmp file.
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).write_bytes(b"re-encrypted-ciphertext")
                    break
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_doctor(backend, ["--fix"])
    assert rc == 0

    final_recipients = (
        (tmp_path / "config" / "env" / ".age-recipients").read_text().splitlines()
    )
    assert "age1meta" in final_recipients
    assert "age1userpub" in final_recipients

    # Each entry got re-encrypted to BOTH recipients.
    assert len(age_invocations) == 2
    for cmd, _stdin in age_invocations:
        rs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-r"]
        assert "age1meta" in rs
        assert "age1userpub" in rs

    # Output files reflect the new ciphertext.
    assert (tmp_path / "config" / "env" / "claude.age").read_bytes() == (
        b"re-encrypted-ciphertext"
    )
    out = capsys.readouterr().out
    assert "Repairs applied" in out
    assert "age1userpub" in out


def test_do_doctor_fix_no_op_when_clean(mocker: MockerFixture, tmp_path: Path) -> None:
    """`--fix` on an already-correct keystore should be a no-op."""
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    identities = tmp_path / "identities"
    identities.write_text("AGE-SECRET-KEY-1USER\n")
    (tmp_path / "config" / "env" / ".age-recipients").write_text(
        "age1userpub\nage1meta\n"
    )
    mocker.patch.dict("os.environ", {"PASSAGE_IDENTITIES_FILE": str(identities)})

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-1FAKE"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("shutil.which", return_value="/usr/bin/age-keygen")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["age-keygen", "-y"]:
            piped = kwargs.get("input") or ""
            assert isinstance(piped, str)
            if "USER" in piped:
                return subprocess.CompletedProcess(cmd, 0, "age1userpub\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1meta\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_doctor(backend, ["--fix"])
    assert rc == 0


def test_classify_pubkey_classic() -> None:
    assert _classify_pubkey("age1u7kymyknstr8wm9fr3za5vncph7q560") == "classic"


def test_classify_pubkey_pq() -> None:
    assert _classify_pubkey("age1pq13v5u7z5qe0l8w2rsgkapsecasmj") == "pq"


def test_classify_pubkey_plugin_is_classic() -> None:
    # Plugin recipients (yubikey, se, etc.) sit on the classic side of age's
    # PQ/classic mixing rule.
    assert _classify_pubkey("age1yubikey1qwerty") == "classic"
    assert _classify_pubkey("age1se1plugin") == "classic"


def test_classify_recipients_homogeneous_and_mixed() -> None:
    assert _classify_recipients(["age1classic1", "age1classic2"]) == {"classic"}
    assert _classify_recipients(["age1pq1one", "age1pq1two"]) == {"pq"}
    assert _classify_recipients(["age1classic", "age1pq1mixed"]) == {"classic", "pq"}
    assert _classify_recipients([]) == set()


def test_do_bootstrap_generates_pq_meta_when_recipients_are_pq(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1pq1userpq\n")

    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-PQ-1NEW\n", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pq1metapub\n", "")
        if cmd[0] == "age-keygen":
            raise AssertionError("bootstrap MUST request -pq when recipients are PQ")
        if cmd[0] == "passage":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 0
    contents = (
        (tmp_path / "config" / "env" / ".age-recipients").read_text().splitlines()
    )
    assert "age1pq1userpq" in contents
    assert "age1pq1metapub" in contents


def test_do_bootstrap_aborts_on_mixed_recipients(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text(
        "age1classic\nage1pq1other\n"
    )

    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )
    mocker.patch(
        "subprocess.run",
        side_effect=AssertionError("should abort before shelling out"),
    )

    rc = do_bootstrap(backend, [])
    assert rc == 1
    err = capsys.readouterr().err
    assert "mix post-quantum and classic" in err


def test_do_rotate_meta_matches_pq_residue(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    recipients = tmp_path / "config" / "env" / ".age-recipients"
    recipients.write_text("age1pq1userpq\nage1pq1oldmeta\n")

    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "age", "key": "AGE-SECRET-KEY-PQ-1OLD"}',
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-PQ-1NEW\n", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            stdin_text = kwargs.get("input") or ""
            assert isinstance(stdin_text, str)
            if "OLD" in stdin_text:
                return subprocess.CompletedProcess(cmd, 0, "age1pq1oldmeta\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1pq1newmeta\n", "")
        if cmd[0] == "age-keygen":
            raise AssertionError("rotate-meta MUST request -pq when residue is PQ")
        if cmd[0] == "passage":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_rotate_meta(backend, ["--yes"])
    assert rc == 0
    contents = recipients.read_text().splitlines()
    assert "age1pq1userpq" in contents
    assert "age1pq1newmeta" in contents
    assert "age1pq1oldmeta" not in contents


def test_do_rotate_meta_aborts_when_residue_empty(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # .age-recipients contains ONLY the old meta pubkey (the broken-bootstrap
    # state). Rotating without inherited recipients in the file would just
    # produce another locked-out state — bail with guidance.
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1oldmeta\n")

    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "age", "key": "AGE-SECRET-KEY-1OLD"}',
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: (
            f"/usr/bin/{name}" if name in {"age-keygen", "passage"} else None
        ),
    )

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["age-keygen", "-y"]:
            return subprocess.CompletedProcess(cmd, 0, "age1oldmeta\n", "")
        if cmd[0] == "age-keygen":
            raise AssertionError("should abort before generating new key")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_rotate_meta(backend, ["--yes"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no non-meta recipients" in err


def test_do_doctor_fix_rotates_meta_when_types_incompatible(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The user's real-world scenario: bootstrap (with the original bug) wrote
    `.age-recipients` containing only a PQ meta pubkey, encrypting all entries
    to that PQ key. The user's actual identity is classic, so the inherited
    pubkey is classic — irreconcilable with the PQ meta. `doctor --fix` must
    detect this and rotate the meta to a classic key as part of the repair."""
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    identities = tmp_path / "identities"
    identities.write_text("AGE-SECRET-KEY-1USER\n")
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1pq1meta\n")
    (tmp_path / "config" / "env" / "claude.age").write_bytes(b"original-ciphertext")
    mocker.patch.dict("os.environ", {"PASSAGE_IDENTITIES_FILE": str(identities)})

    blobs = {"config/env-meta": '{"backend": "age", "key": "AGE-SECRET-KEY-PQ-1OLD"}'}
    mocker.patch.object(Backend, "show", side_effect=lambda p: blobs.get(p))
    mocker.patch("shutil.which", return_value="/usr/bin/age")

    decrypted = {"claude": "FOO=claude\n"}
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=lambda _store, name, _ext: decrypted[name]
    )

    age_invocations: list[tuple[list[str], bytes | None]] = []
    insert_calls: list[tuple[list[str], str | None]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["age-keygen", "-y"]:
            piped = kwargs.get("input") or ""
            assert isinstance(piped, str)
            if "USER" in piped:
                return subprocess.CompletedProcess(cmd, 0, "age1userpub\n", "")
            if "OLD" in piped:
                return subprocess.CompletedProcess(cmd, 0, "age1pq1meta\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1newclassicmeta\n", "")
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            raise AssertionError(
                "doctor --fix should NOT request -pq when inherited is classic"
            )
        if cmd[0] == "age-keygen":
            return subprocess.CompletedProcess(
                cmd, 0, "AGE-SECRET-KEY-1NEWCLASSIC\n", ""
            )
        if cmd[0] == "age" and "-e" in cmd:
            age_invocations.append((cmd, kwargs.get("input")))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            for i, arg in enumerate(cmd):
                if arg == "-o" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).write_bytes(b"re-encrypted-ciphertext")
                    break
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "passage" and cmd[1] == "insert":
            insert_calls.append((cmd, kwargs.get("input")))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_doctor(backend, ["--fix"])
    assert rc == 0

    final_recipients = (
        (tmp_path / "config" / "env" / ".age-recipients").read_text().splitlines()
    )
    assert "age1pq1meta" not in final_recipients
    assert "age1userpub" in final_recipients
    assert "age1newclassicmeta" in final_recipients

    assert len(age_invocations) == 1
    cmd, _stdin = age_invocations[0]
    rs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-r"]
    assert "age1pq1meta" not in rs
    assert "age1userpub" in rs
    assert "age1newclassicmeta" in rs

    assert insert_calls, "passage insert should have been invoked"
    insert_cmd, insert_stdin = insert_calls[0]
    assert "--force" in insert_cmd
    assert isinstance(insert_stdin, str)
    payload = json.loads(insert_stdin)
    assert payload["key"] == "AGE-SECRET-KEY-1NEWCLASSIC"

    out = capsys.readouterr().out
    assert "Repairs applied" in out
    assert "rotated meta key" in out
    assert "pq" in out and "classic" in out


# --- pass backend: rotate-meta ---------------------------------------------


def _which_gpg_pass(name: str) -> str | None:
    return f"/usr/bin/{name}" if name in {"gpg", "pass"} else None


def test_do_rotate_meta_pass_without_yes_describes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    rc = do_rotate_meta(backend, [])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rotate-meta will" in out.lower()
    assert "--yes" in out
    assert "gpg meta key" in out


def test_do_rotate_meta_pass_no_existing_meta(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch("shutil.which", side_effect=_which_gpg_pass)
    mocker.patch.object(Backend, "show", return_value=None)
    rc = do_rotate_meta(backend, ["--yes"])
    assert rc == 1
    assert "no config/env-meta found" in capsys.readouterr().err


def test_do_rotate_meta_pass_happy_path(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = _make_pass_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".gpg-id").write_text("USERKEY\nOLDFPR\n")
    mocker.patch("shutil.which", side_effect=_which_gpg_pass)
    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "gpg", "passphrase": "p", "key": "OLDSECRET"}',
    )
    mocker.patch("wlrenv.secwrap._gpg_meta_identity", return_value=("OLDFPR", "OLDPUB"))
    mocker.patch(
        "wlrenv.secwrap._gpg_generate_meta_key",
        return_value=("NEWFPR", "NEWSECRET", "NEWPUB", "NEWPASS"),
    )
    import_mock = mocker.patch("wlrenv.secwrap._import_meta_pubkey")
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "pass" and cmd[1] == "init":
            captured["init"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "pass" and cmd[1] == "insert":
            captured["insert_input"] = kwargs.get("input")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_rotate_meta(backend, ["--yes"])
    assert rc == 0
    # New meta pubkey imported + trusted.
    import_mock.assert_called_once_with("NEWFPR", "NEWPUB")
    # Old meta fingerprint dropped, user key kept, new meta added.
    assert captured["init"] == [
        "pass",
        "init",
        "-p",
        "config/env",
        "USERKEY",
        "NEWFPR",
    ]
    payload = json.loads(captured["insert_input"])
    assert payload["backend"] == "gpg"
    assert payload["key"] == "NEWSECRET"
    assert payload["passphrase"] == "NEWPASS"  # noqa: S105 - test literal, not a secret


def test_do_rotate_meta_pass_empty_residue_aborts(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    # config/env/.gpg-id lists ONLY the meta key -> rotating would lock the user out.
    (tmp_path / "config" / "env" / ".gpg-id").write_text("OLDFPR\n")
    mocker.patch("shutil.which", side_effect=_which_gpg_pass)
    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "gpg", "passphrase": "p", "key": "OLDSECRET"}',
    )
    mocker.patch("wlrenv.secwrap._gpg_meta_identity", return_value=("OLDFPR", "OLDPUB"))
    rc = do_rotate_meta(backend, ["--yes"])
    assert rc == 1
    assert "no non-meta recipients" in capsys.readouterr().err


# --- pass backend: doctor --------------------------------------------------


def _seed_pass_store(tmp_path: Path, *, local_gpg_id: str, root_gpg_id: str) -> None:
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / ".gpg-id").write_text(root_gpg_id)
    (tmp_path / "config" / "env" / ".gpg-id").write_text(local_gpg_id)
    (tmp_path / "config" / "env" / "claude.gpg").write_text("ciphertext")


def test_do_doctor_pass_clean(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    _seed_pass_store(
        tmp_path, local_gpg_id="USERKEY\nMETAFPR\n", root_gpg_id="USERKEY\n"
    )
    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "gpg", "passphrase": "p", "key": "SECRET"}',
    )
    mocker.patch(
        "wlrenv.secwrap._gpg_meta_identity", return_value=("METAFPR", "METAPUB")
    )
    mocker.patch.object(MetaKey, "decrypt", return_value="FOO=bar\n")
    rc = do_doctor(backend, [])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Checking config/env/.gpg-id" in out
    assert "All checks passed." in out


def test_do_doctor_pass_missing_meta_fingerprint(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    _seed_pass_store(tmp_path, local_gpg_id="USERKEY\n", root_gpg_id="USERKEY\n")
    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "gpg", "passphrase": "p", "key": "SECRET"}',
    )
    mocker.patch(
        "wlrenv.secwrap._gpg_meta_identity", return_value=("METAFPR", "METAPUB")
    )
    mocker.patch.object(MetaKey, "decrypt", return_value="FOO=bar\n")
    rc = do_doctor(backend, [])
    assert rc == 1
    assert (
        "meta fingerprint METAFPR not in config/env/.gpg-id" in capsys.readouterr().err
    )


def test_do_doctor_pass_fix_repairs_missing_inherited(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    # Local .gpg-id has the meta key but is missing the inherited user key.
    _seed_pass_store(tmp_path, local_gpg_id="METAFPR\n", root_gpg_id="USERKEY\n")
    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "gpg", "passphrase": "p", "key": "SECRET"}',
    )
    mocker.patch(
        "wlrenv.secwrap._gpg_meta_identity", return_value=("METAFPR", "METAPUB")
    )
    mocker.patch.object(MetaKey, "decrypt", return_value="FOO=bar\n")
    import_mock = mocker.patch("wlrenv.secwrap._import_meta_pubkey")
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "pass" and cmd[1] == "init":
            captured["init"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_doctor(backend, ["--fix"])
    assert rc == 0
    import_mock.assert_called_once_with("METAFPR", "METAPUB")
    # Repair re-inits config/env to the union (sorted), restoring the user key.
    assert captured["init"] == [
        "pass",
        "init",
        "-p",
        "config/env",
        "METAFPR",
        "USERKEY",
    ]
    out = capsys.readouterr().out
    assert "Repairs applied" in out


def test_do_doctor_pass_undecryptable_entry(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    _seed_pass_store(
        tmp_path, local_gpg_id="USERKEY\nMETAFPR\n", root_gpg_id="USERKEY\n"
    )
    mocker.patch.object(
        Backend,
        "show",
        return_value='{"backend": "gpg", "passphrase": "p", "key": "SECRET"}',
    )
    mocker.patch(
        "wlrenv.secwrap._gpg_meta_identity", return_value=("METAFPR", "METAPUB")
    )
    mocker.patch.object(
        MetaKey, "decrypt", side_effect=MetaKeyError("gpg decryption failed for claude")
    )
    rc = do_doctor(backend, [])
    assert rc == 1
    assert "entry claude failed to decrypt" in capsys.readouterr().err
