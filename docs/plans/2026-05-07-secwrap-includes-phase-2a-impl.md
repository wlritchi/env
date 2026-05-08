# `secwrap` Phase 2a (includes + marker) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add include-comment graph traversal, env merging across the transitive closure of an entry's dependencies, and the `_SECWRAP_LOADED` marker. After this phase: `secwrap claude` pre-loads docker/pnpm secrets when claude declares them as includes, and a nested `secwrap pnpm` (e.g. spawned from inside claude) short-circuits when pnpm is already in the marker.

**Architecture:** Pure functions for include-comment parsing and marker (de)serialization; a `resolve_includes` graph walker with cycle detection. `main()` reads the marker before backend detection for the short-circuit path; on the full wrap path it runs the resolver, merges KEY=VALUE in topological order (deepest-first, root last; sibling alphabetical), updates the marker, and exec's. On the `pass` backend, include comments are detected and degraded with a stderr warning per spec — the marker still works.

**Tech Stack:** Python 3.12, pytest, pytest-mock.

**Spec reference:** `docs/specs/2026-05-07-secwrap-includes-design.md` — sections "Include Comments", "Loaded Marker", "Runtime Flow", "Failure Modes".

**Out of scope (deferred to later phases):**
- Phase 2b: passage age meta key, `secwrap bootstrap`/`rotate-meta`/`doctor` subcommands.
- Phase 3: pass gpg meta-key flow.

---

## Plan decisions (locking down spec ambiguities)

1. **Skip already-marker-loaded entries during the include walk.** When resolving the include graph, an entry whose name appears in the inherited `_SECWRAP_LOADED` marker is *not* re-decrypted, but its name *is* included in the new marker that we export. Rationale: avoid redundant prompts when an outer wrap already loaded the dep.
2. **`pass` backend: ignore include comments with one warning per entry that contains them.** Implementation: load only the root entry; if the root blob contains include comments, emit `secwrap: include comments are not yet implemented for the pass backend; ignoring` to stderr (once for that invocation). The marker continues to work on `pass`. Rationale: without the meta key, walking the graph would prompt N times — exactly the friction we're trying to eliminate.
3. **Marker update happens only on the wrap path.** `--list` and `--help` don't touch the marker. The short-circuit path (target ∈ marker) doesn't update either — it's a pass-through.
4. **Malformed marker tokens are filtered out, not "treat the whole marker as empty".** If the user manually set `_SECWRAP_LOADED=foo bar:claude` (bad tokens with whitespace), `claude` is still recognized; `foo bar` is dropped. Pure cleanup, no warning.
5. **Invalid tool names in `# secwrap-include:` lines are silently dropped.** Tokens that don't match `[A-Za-z0-9._-]+` after whitespace splitting are skipped. Mirrors `parse_env_lines`'s "garbage in, dropped silently" behavior.
6. **A missing root entry is not an error.** Same as Phase 1: if `config/env/<command>` doesn't exist, exec the command unmodified. Includes are only walked for an existing root.
7. **A missing *include* IS a hard error.** Spec failure mode: `secwrap: claude includes 'pnpm' but config/env/pnpm not found`.

---

## File structure

| Path                                  | Action  | Responsibility                                                                       |
|---------------------------------------|---------|--------------------------------------------------------------------------------------|
| `src/wlrenv/secwrap.py`               | Modify  | Add `parse_includes`, `parse_marker`, `format_marker`, `IncludeError`, `resolve_includes`; rework `main()`'s wrap branch. |
| `tests/test_secwrap.py`               | Modify  | Tests for new pure functions, the resolver, and `main()` integration paths.           |

---

## Tasks

### Task 1: Implement and test `parse_includes`

`parse_includes(content: str) -> list[str]` extracts include names from `# secwrap-include: <tool> [<tool>...]` lines. Returns the list in *document order with duplicates preserved* — the resolver later dedupes via the visited set and sorts siblings alphabetically.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests for `parse_includes`**

Append to `tests/test_secwrap.py`:

```python
from wlrenv.secwrap import parse_includes


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
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v -k parse_includes`
Expected: 10 new tests fail with `ImportError: cannot import name 'parse_includes'`.

- [ ] **Step 3: Implement `parse_includes`**

Add to `src/wlrenv/secwrap.py`, alongside the existing `_ENV_LINE` regex and `parse_env_lines` function:

```python
_INCLUDE_LINE = re.compile(r"^\s*#\s*secwrap-include:\s*(.+?)\s*$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_includes(content: str) -> list[str]:
    """Extract tool names from `# secwrap-include: ...` directives.

    Returns names in document order; duplicates preserved (resolver dedupes).
    Tokens not matching `[A-Za-z0-9._-]+` are dropped silently.
    """
    out: list[str] = []
    for raw_line in content.split("\n"):
        m = _INCLUDE_LINE.match(raw_line)
        if m is None:
            continue
        for token in m.group(1).split():
            if _TOOL_NAME.match(token):
                out.append(token)
    return out
```

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 53 passed (43 prior + 10 new).

- [ ] **Step 5: Run autoformatter and pyright**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): parse_includes extracts deps from secwrap-include comments

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Implement and test marker helpers (`parse_marker`, `format_marker`)

`parse_marker(value: str) -> set[str]` parses a colon-separated `_SECWRAP_LOADED` value, dropping tokens that don't match `[A-Za-z0-9._-]+`. `format_marker(names: Iterable[str]) -> str` produces the canonical alphabetized, deduped, colon-joined form.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_secwrap.py`:

```python
from wlrenv.secwrap import format_marker, parse_marker


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
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v -k marker`
Expected: 9 new tests fail with `ImportError`.

- [ ] **Step 3: Implement `parse_marker` and `format_marker`**

Add to `src/wlrenv/secwrap.py` (near `parse_includes`, since both deal with the marker/include datamodel). Add `from collections.abc import Iterable` to the top-of-file imports.

```python
def parse_marker(value: str) -> set[str]:
    """Parse a `_SECWRAP_LOADED` value into a set of tool names.

    Tokens not matching `[A-Za-z0-9._-]+` are dropped silently. Empty input
    returns the empty set.
    """
    if not value:
        return set()
    return {tok for tok in value.split(":") if _TOOL_NAME.match(tok)}


def format_marker(names: Iterable[str]) -> str:
    """Format a set of tool names as a canonical `_SECWRAP_LOADED` value.

    Output is alphabetized, deduped, and colon-joined.
    """
    return ":".join(sorted(set(names)))
```

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 62 passed (53 prior + 9 new).

- [ ] **Step 5: Run autoformatter and pyright**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): _SECWRAP_LOADED marker parse/format helpers

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Implement and test `resolve_includes` (graph walk + cycle detection)

`resolve_includes` walks the include graph from a root entry name and returns a list of `(name, blob)` pairs in **merge order**: deepest dependency first, root last; siblings alphabetical for determinism. Behavior:

- Backend == `passage`: full graph walk.
- Backend == `pass`: load only the root; if its blob contains include comments, emit a one-time warning and proceed with empty includes (Phase 2a degradation).
- Root entry doesn't exist (`backend.show` returns `None`): return `[]`. Caller treats as "no env to merge", same as Phase 1.
- Entry name in `marker_loaded`: skip the `backend.show` call AND the recursion into its includes, but still record the name so the caller can update the marker. Represent in the return as `(name, None)`.
- A non-root entry referenced by an include directive that doesn't resolve: raise `IncludeError("<parent> includes '<name>' but config/env/<name> not found")`.
- Cycle detected: raise `IncludeError("cycle detected: A → B → A")`.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests for `IncludeError` + `resolve_includes`**

Append to `tests/test_secwrap.py`:

```python
from wlrenv.secwrap import IncludeError, resolve_includes


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
    mocker.patch.object(backend, "show", return_value=None)
    assert resolve_includes(backend, "ghost", marker_loaded=set()) == []


def test_resolve_includes_root_with_no_includes(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    mocker.patch.object(backend, "show", return_value="FOO=bar\n")
    assert resolve_includes(backend, "claude", marker_loaded=set()) == [
        ("claude", "FOO=bar\n"),
    ]


def test_resolve_includes_simple_chain(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/claude": "# secwrap-include: docker\nFOO=claude\n",
        "config/env/docker": "BAR=docker\n",
    }
    mocker.patch.object(backend, "show", side_effect=lambda p: blobs.get(p))
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
    mocker.patch.object(backend, "show", side_effect=lambda p: blobs.get(p))
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
    mocker.patch.object(backend, "show", side_effect=lambda p: blobs.get(p))
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
    mocker.patch.object(backend, "show", side_effect=lambda p: blobs.get(p))
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
    mocker.patch.object(backend, "show", side_effect=lambda p: blobs.get(p))
    with pytest.raises(IncludeError, match=r"cycle detected: a → b → a"):
        resolve_includes(backend, "a", marker_loaded=set())


def test_resolve_includes_self_cycle_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    backend = _make_passage_backend(tmp_path)
    blobs = {
        "config/env/a": "# secwrap-include: a\n",
    }
    mocker.patch.object(backend, "show", side_effect=lambda p: blobs.get(p))
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
    show_mock = mocker.patch.object(
        backend, "show", side_effect=lambda p: blobs.get(p)
    )
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
    show_mock = mocker.patch.object(backend, "show")
    result = resolve_includes(backend, "claude", marker_loaded={"claude"})
    assert result == [("claude", None)]
    show_mock.assert_not_called()


def test_resolve_includes_pass_backend_warns_and_ignores(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch.object(
        backend, "show", return_value="# secwrap-include: docker\nFOO=claude\n"
    )
    result = resolve_includes(backend, "claude", marker_loaded=set())
    assert result == [("claude", "# secwrap-include: docker\nFOO=claude\n")]
    err = capsys.readouterr().err
    assert "include comments are not yet implemented for the pass backend" in err


def test_resolve_includes_pass_backend_no_includes_no_warning(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    backend = _make_pass_backend(tmp_path)
    mocker.patch.object(backend, "show", return_value="FOO=bar\n")
    result = resolve_includes(backend, "claude", marker_loaded=set())
    assert result == [("claude", "FOO=bar\n")]
    assert capsys.readouterr().err == ""
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v -k resolve_includes`
Expected: 12 new tests fail with `ImportError`.

- [ ] **Step 3: Implement `IncludeError` and `resolve_includes`**

Add to `src/wlrenv/secwrap.py` (after `Backend`, before `main`):

```python
class IncludeError(RuntimeError):
    """Raised when include resolution fails (cycle, missing dep, etc.)."""


_PASS_INCLUDES_WARNING = (
    "secwrap: include comments are not yet implemented for the pass backend; "
    "ignoring"
)


def resolve_includes(
    backend: Backend, root: str, marker_loaded: set[str]
) -> list[tuple[str, str | None]]:
    """Walk the include graph from `root` and return entries in merge order.

    Returns a list of (name, blob) pairs:
      - Deepest dependency first, root last.
      - Siblings sorted alphabetically.
      - blob is None when the entry was already in `marker_loaded` and skipped.

    The pass backend does NOT walk includes in Phase 2a; it loads only the
    root and emits a one-time stderr warning if the blob contains include
    comments.

    A missing root returns []. A missing non-root include raises IncludeError.
    A cycle raises IncludeError.
    """
    if backend.name == "pass":
        if root in marker_loaded:
            return [(root, None)]
        blob = backend.show(f"config/env/{root}")
        if blob is None:
            return []
        if parse_includes(blob):
            print(_PASS_INCLUDES_WARNING, file=sys.stderr)
        return [(root, blob)]

    # passage: full graph walk.
    result: list[tuple[str, str | None]] = []
    visited: set[str] = set()
    path: list[str] = []

    def visit(name: str, parent: str | None) -> None:
        if name in visited:
            return
        if name in path:
            cycle = " → ".join([*path[path.index(name) :], name])
            raise IncludeError(f"cycle detected: {cycle}")
        path.append(name)
        try:
            if name in marker_loaded:
                visited.add(name)
                result.append((name, None))
                return
            blob = backend.show(f"config/env/{name}")
            if blob is None:
                if parent is None:
                    return  # root missing → caller falls through
                raise IncludeError(
                    f"{parent} includes '{name}' but config/env/{name} not found"
                )
            for dep in sorted(set(parse_includes(blob))):
                visit(dep, parent=name)
            visited.add(name)
            result.append((name, blob))
        finally:
            path.pop()

    visit(root, parent=None)
    return result
```

Note on the self-cycle case (`a -> a`): the algorithm pushes `a` onto `path`, recurses into `a` again, sees `a` in `path`, and raises `cycle detected: a → a`. Confirmed by test.

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 74 passed (62 prior + 12 new).

- [ ] **Step 5: Run autoformatter and pyright**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): resolve_includes walks the dep graph with cycle detection

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Wire marker short-circuit and include resolution into `main()`

This task replaces the wrap branch of `main()` end-to-end:

1. After `parse_args` succeeds and we know the secret_key, parse the inherited `_SECWRAP_LOADED` marker. **Before backend detection**, check the short-circuit: if `secret_key in marker_loaded`, exec immediately with `os.execvpe(args.command, [args.command, *args.forwarded], os.environ)` (using the *current* env, including the inherited marker). Skip backend detection entirely — this is the cheapest possible path.
2. Otherwise: detect backend, resolve includes, merge KEY=VALUE in the order returned by `resolve_includes` (deepest first, root last — meaning later writes win, so the root's values override its includes' values on key conflicts). Skipped (marker-loaded) entries contribute no merges.
3. Build the new marker as `format_marker(marker_loaded | {names from resolved})`. Set it in env.
4. Exec.

Failure handling:
- `IncludeError` → `secwrap: <message>` to stderr, exit 1.
- `BackendError` → unchanged from Phase 1.
- `--list` and `--help` paths unchanged.

**Files:**
- Modify: `src/wlrenv/secwrap.py`
- Modify: `tests/test_secwrap.py`

- [ ] **Step 1: Append failing tests for the new `main()` behavior**

Append to `tests/test_secwrap.py`:

```python
def test_main_wrap_short_circuits_when_target_in_marker(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # If pnpm is already in the marker, secwrap should NOT detect the backend
    # and should NOT call show.
    mocker.patch.dict(
        "os.environ",
        {"_SECWRAP_LOADED": "claude:pnpm", "PATH": "/usr/bin"},
        clear=True,
    )
    detect_mock = mocker.patch("wlrenv.secwrap.Backend.detect")
    execvpe = mocker.patch("os.execvpe")

    from wlrenv.secwrap import main

    main(["pnpm", "install"])

    detect_mock.assert_not_called()
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
    # `secwrap --from claude bar` short-circuits.
    mocker.patch.dict(
        "os.environ",
        {"_SECWRAP_LOADED": "claude", "PATH": "/usr/bin"},
        clear=True,
    )
    detect_mock = mocker.patch("wlrenv.secwrap.Backend.detect")
    execvpe = mocker.patch("os.execvpe")

    from wlrenv.secwrap import main

    main(["--from", "claude", "bar"])

    detect_mock.assert_not_called()
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

    from wlrenv.secwrap import main

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

    from wlrenv.secwrap import main

    main(["claude"])

    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["_SECWRAP_LOADED"] == "claude:existing"


def test_main_wrap_no_entry_still_sets_marker(
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

    from wlrenv.secwrap import main

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

    from wlrenv.secwrap import main

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

    from wlrenv.secwrap import main

    rc = main(["a"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "cycle detected" in err


def test_main_wrap_marker_skip_does_not_re_decrypt(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    # claude includes docker; docker is already in marker. show should be
    # called for claude only.
    blobs = {
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

    from wlrenv.secwrap import main

    main(["claude"])

    fetched = [c.args[0] for c in show_mock.call_args_list]
    assert fetched == ["config/env/claude"]
    _file, _argv, env_arg = execvpe.call_args.args
    assert env_arg["_SECWRAP_LOADED"] == "claude:docker"
    assert env_arg["FOO"] == "bar"
```

- [ ] **Step 2: Run tests; confirm new tests fail**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: previous 74 still pass; the 8 new tests fail (because `main()` doesn't have the new logic yet).

- [ ] **Step 3: Rewrite the wrap branch of `main()`**

Replace the wrap branch in `src/wlrenv/secwrap.py` `main()`. The full updated function:

```python
def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        args = parse_args(argv)
    except ArgError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 1

    if args.help_mode:
        print(USAGE)
        return 0

    if args.command is None and not args.list_mode:
        print(USAGE, file=sys.stderr)
        return 1

    # Marker short-circuit: if the secret_key is already loaded, exec
    # immediately without touching the backend.
    if args.command is not None and not args.list_mode:
        secret_key = (
            args.from_name if args.from_name is not None else args.command
        )
        marker_loaded = parse_marker(os.environ.get("_SECWRAP_LOADED", ""))
        if secret_key in marker_loaded:
            os.execvpe(args.command, [args.command, *args.forwarded], os.environ)  # noqa: S606
            return 0  # unreachable; satisfies type checker

    try:
        backend = Backend.detect()
    except BackendError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1

    if args.list_mode:
        for tool in backend.list_tools():
            print(tool)
        return 0

    assert args.command is not None  # narrowed above
    secret_key = args.from_name if args.from_name is not None else args.command
    marker_loaded = parse_marker(os.environ.get("_SECWRAP_LOADED", ""))

    try:
        resolved = resolve_includes(backend, secret_key, marker_loaded)
    except IncludeError as exc:
        print(f"secwrap: {exc}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    for _name, blob in resolved:
        if blob is not None:
            env.update(parse_env_lines(blob))

    if resolved:
        new_marker = format_marker(marker_loaded | {name for name, _ in resolved})
        env["_SECWRAP_LOADED"] = new_marker

    os.execvpe(args.command, [args.command, *args.forwarded], env)  # noqa: S606
    return 0  # unreachable; satisfies type checker
```

Key behaviors:
- The short-circuit branch reads `os.environ` directly (not a copy), since we're about to exec anyway.
- The "marker is set only if `resolved` is non-empty" rule preserves Phase 1 behavior for the no-entry case (no spurious `_SECWRAP_LOADED=foo` for tools we didn't load anything for).
- The marker short-circuit is gated on `args.command is not None and not args.list_mode` to keep the help/list paths working without modification.

- [ ] **Step 4: Run tests; confirm all pass**

Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pytest tests/test_secwrap.py -v`
Expected: 82 passed (74 prior + 8 new).

If any prior test fails, the most likely cause is that some Phase 1 main() test sets `SECWRAP_BACKEND=passage` but doesn't seed `_SECWRAP_LOADED` — that should still work because `parse_marker("")` returns `set()` and `"<command>" in set()` is False. Re-check the test fixtures if you see regressions.

- [ ] **Step 5: Run autoformatter and pyright**

Run: `cd /home/wlritchi/.wlrenv && uv tool run ruff format src/wlrenv/secwrap.py tests/test_secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev pyright src/wlrenv/secwrap.py`
Run: `cd /home/wlritchi/.wlrenv && uv run --group dev ruff check src/wlrenv/secwrap.py tests/test_secwrap.py`
Expected: 0 errors, 0 warnings.

- [ ] **Step 6: Commit**

```bash
cd /home/wlritchi/.wlrenv && git add src/wlrenv/secwrap.py tests/test_secwrap.py && git commit -m "$(cat <<'EOF'
feat(secwrap): wire marker short-circuit and include resolution into main()

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Smoke-test the installed entry point (regression check)

Phase 2a doesn't add live store dependencies (this machine still has no `config/env/*` entries), so the smoke test just verifies that the existing wrap-no-entry path and `--help`/`--list` flows still work, plus a marker pass-through.

**Files:** none modified.

- [ ] **Step 1: Reinstall the wlrenv tool**

Run: `uv tool install --reinstall /home/wlritchi/.wlrenv/ --python 3.12`
Expected: exit 0; `Installed N executables: ..., secwrap, ...`.

- [ ] **Step 2: --help and --list still work**

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap --help`
Expected: USAGE printed; exit 0.

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap --list`
Expected: empty output (no entries on this machine); exit 0.

- [ ] **Step 3: Wrap-no-entry path still works**

Run: `~/.local/share/uv/tools/wlrenv/bin/secwrap env | grep -E '^(HOME|PATH)='`
Expected: HOME and PATH lines present; exit 0.

- [ ] **Step 4: Marker short-circuit path with synthetic marker**

Run: `_SECWRAP_LOADED=foo ~/.local/share/uv/tools/wlrenv/bin/secwrap foo true && echo OK`
Expected: `OK`. (foo is in the marker, so we short-circuit to exec'ing `true` — which exits 0 — without touching the backend.)

Run: `_SECWRAP_LOADED=foo ~/.local/share/uv/tools/wlrenv/bin/secwrap env | grep _SECWRAP_LOADED`
Expected: `_SECWRAP_LOADED=foo` (the marker is preserved through the short-circuit when target is "env" — wait, "env" is not in the marker, so this goes through the wrap path. The marker still gets passed because we don't have an entry for env).

Actually for clarity, the second test should explicitly invoke a tool that *is* in the marker:
Run: `_SECWRAP_LOADED=true ~/.local/share/uv/tools/wlrenv/bin/secwrap true && echo OK`
Expected: `OK`.

- [ ] **Step 5: No commit (verification only)**

If any verification fails, stop and diagnose; the commit-bearing tasks are 1–4.

---

## Self-review notes

**Spec coverage:**
- Include comments: parsed (Task 1), walked (Task 3), wired (Task 4). ✓
- Marker: parsed/formatted (Task 2), short-circuited and updated (Task 4). ✓
- Cycle detection + missing-include hard error: Task 3. ✓
- Conflict resolution (deepest-first, root last): Task 4 merge loop. ✓
- pass backend warn-and-ignore: Task 3. ✓
- pass backend marker still works: Task 4 (the short-circuit branch is backend-agnostic). ✓
- Marker malformed → treat as empty: Task 2 (`parse_marker` filters bad tokens; resulting set is treated normally). ✓
- `--from` interaction: Task 4 (secret_key uses `args.from_name if not None else args.command`; marker keys on secret_key). ✓

**Type consistency:** `parse_includes`, `parse_marker`, `format_marker`, `IncludeError`, `resolve_includes` — names consistent across tasks. `IncludeError` is a sibling of `BackendError`/`ArgError`, treated identically by `main()`'s error rendering.

**Placeholder scan:** none — every step has either concrete code, a concrete command, or a precise edit instruction.

**Out-of-scope confirmation:** no meta-key flow, no `bootstrap`/`rotate-meta`/`doctor` subcommands. Phase 2b will add these.
