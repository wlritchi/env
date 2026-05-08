from __future__ import annotations

import json
import subprocess
from pathlib import Path
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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


def test_resolve_includes_pass_backend_warns_and_ignores(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch.object(
        Backend, "show", return_value="# secwrap-include: docker\nFOO=claude\n"
    )
    result = resolve_includes(backend, "claude", marker_loaded=set())
    assert result == [("claude", "# secwrap-include: docker\nFOO=claude\n")]
    err = capsys.readouterr().err
    assert "include comments are not yet implemented for the pass backend" in err


def test_resolve_includes_pass_backend_no_includes_no_warning(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch.object(Backend, "show", return_value="FOO=bar\n")
    result = resolve_includes(backend, "claude", marker_loaded=set())
    assert result == [("claude", "FOO=bar\n")]
    assert capsys.readouterr().err == ""


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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
    )
    do_doctor = mocker.patch("wlrenv.secwrap.do_doctor", return_value=0)

    rc = main(["doctor"])

    assert rc == 0
    do_doctor.assert_called_once()


def test_main_subcommand_pass_backend_exits_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
    )
    do_bootstrap = mocker.patch("wlrenv.secwrap.do_bootstrap")

    rc = main(["bootstrap"])

    assert rc == 1
    do_bootstrap.assert_not_called()
    err = capsys.readouterr().err
    assert "not yet supported for the pass backend" in err
    assert "Phase 3" in err


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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"pass", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"age-keygen", "passage"}
        else None,
    )

    # Mock subprocess.run for each shell-out.
    insert_inputs: list[str | None] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            # Key emitted on stdout (no -o flag).
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-1FAKE\n", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pub...\n", "")
        if cmd[0] == "passage" and cmd[1] == "reencrypt":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "passage" and cmd[1] == "insert":
            insert_inputs.append(kwargs.get("input"))  # type: ignore[arg-type]
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


def test_do_bootstrap_meta_already_exists(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(
        Backend, "show", return_value='{"backend": "age", "key": "..."}'
    )
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"age-keygen", "passage"}
        else None,
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


def test_do_bootstrap_keygen_fallback_to_x25519(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # First age-keygen with -pq fails (older age); second without -pq succeeds.
    backend = _make_passage_backend(tmp_path)
    (tmp_path / "config" / "env").mkdir(parents=True)
    (tmp_path / "config" / "env" / ".age-recipients").write_text("age1user\n")

    mocker.patch.object(Backend, "show", return_value=None)
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"age-keygen", "passage"}
        else None,
    )

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "unrecognized flag: -pq\n")
        if cmd[0] == "age-keygen" and "-pq" not in cmd and "-y" not in cmd:
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-1FALLBACK\n", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "age1pub...\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch("subprocess.run", side_effect=fake_run)

    rc = do_bootstrap(backend, [])
    assert rc == 0


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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"age-keygen", "passage"}
        else None,
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
        side_effect=lambda name: f"/usr/bin/{name}"
        if name in {"age-keygen", "passage"}
        else None,
    )

    insert_calls: list[tuple[list[str], str | None]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "age-keygen" and "-pq" in cmd:
            # Generate new key on stdout.
            return subprocess.CompletedProcess(cmd, 0, "AGE-SECRET-KEY-1NEW\n", "")
        if cmd[0] == "age-keygen" and "-y" in cmd:
            # Distinguish OLD vs NEW based on the key piped in via stdin.
            stdin_text = kwargs.get("input") or ""
            assert isinstance(stdin_text, str)
            if "OLD" in stdin_text:
                return subprocess.CompletedProcess(cmd, 0, "age1oldmeta\n", "")
            return subprocess.CompletedProcess(cmd, 0, "age1newmeta\n", "")
        if cmd[0] == "passage" and cmd[1] == "insert":
            insert_calls.append((cmd, kwargs.get("input")))  # type: ignore[arg-type]
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
