# Unified niri Window Tracking Design

## Overview

A unified system for tracking and restoring window metadata (workspace, width) across multiple applications: tmux, mosh, and Librewolf. Replaces fragmented bash scripts with a cohesive Python module.

## Goals

1. **DRY**: Extract shared tracking/storage logic into common modules
2. **Clean mental model**: One storage format, consistent patterns across apps
3. **Extensibility**: Easy to add new apps (zellij, k9s, etc.) with minimal code

## Architecture

### Layer 0: niri IPC

**Module:** `src/wlrenv/niri/ipc.py`

```python
def get_windows(app_id: str | None = None) -> list[Window]:
    """Query niri for windows, optionally filtered by app_id."""

def get_outputs() -> list[Output]:
    """Query outputs with logical dimensions."""

def get_workspaces() -> list[Workspace]:
    """Query workspaces with output mapping."""

def find_window_by_title(title: str) -> Window | None:
    """Find window matching title."""

def wait_for_window(pid: int, timeout: float = 5.0) -> int:
    """Poll until window with PID appears, return window_id."""

def configure(window_id: int, workspace: int | None, width: int | None) -> None:
    """Move window to workspace and/or set width percentage."""
```

### Layer 1: Storage

**Module:** `src/wlrenv/niri/storage.py`

**Location:** `~/.local/state/niri/<app>.json`

**Schema:**
```json
{
  "version": 1,
  "entries": {
    "<identity>": {"workspace": 2, "width": 50}
  }
}
```

**Identity by app:**
- tmux: session name (e.g., `"dotfiles"`)
- mosh: `"host:session"` (e.g., `"server.example.com:main"`)
- librewolf: UUID (assigned via URL matching)

**API:**
```python
def store(app: str, identity: str, window_id: int) -> None:
    """Query window's current workspace/width from niri, persist to JSON."""

def lookup(app: str, identity: str) -> dict | None:
    """Return {"workspace": N, "width": M} or None."""
```

**Atomic writes:** Write to tempfile, then rename to avoid corruption.

### Layer 2a: Identification (Tracking)

**Terminal apps** (`src/wlrenv/niri/identify.py`):

```python
def identify_tmux(proc: Process) -> str | None:
    """Parse tmux session from child process args (-t session)."""

def identify_mosh(proc: Process) -> str | None:
    """Parse host:session from moshen process args."""
```

**Librewolf** (`src/wlrenv/niri/librewolf.py`):

URL-based UUID matching with greedy assignment:

```python
class UrlMatcher:
    """Stateful matcher - tracks available entries within a batch operation."""

    @classmethod
    def load(cls) -> "UrlMatcher":
        """Load from ~/.local/state/niri/librewolf-identities.json"""

    def match_or_create(self, urls: list[str]) -> str:
        """Find entry with max URL overlap from remaining pool, or create new UUID."""

    def save(self) -> None:
        """Persist updated entries."""
```

**Librewolf identity storage:** `~/.local/state/niri/librewolf-identities.json`
```json
{
  "entries": [
    {"uuid": "abc-123", "urls": ["https://github.com/...", "https://..."]}
  ]
}
```

### Layer 2b: Tracking Orchestration

**Unified terminal tracker** (`src/wlrenv/niri/track.py`):

```python
def track_terminals() -> None:
    windows = niri.get_windows(app_id="Alacritty")

    for window in windows:
        for child in get_child_processes(window.pid):
            if identity := identify_tmux(child):
                storage.store("tmux", identity, window.id)
                break
            if identity := identify_mosh(child):
                storage.store("mosh", identity, window.id)
                break
            # Extensible: add more parsers here
```

**Librewolf tracking** (in native messaging host):

```python
def handle_store_message(windows: list[dict]) -> None:
    windows = sorted(windows, key=lambda w: len(w["urls"]), reverse=True)

    matcher = UrlMatcher.load()

    for win in windows:
        uuid = matcher.match_or_create(win["urls"])
        niri_window = niri.find_window_by_title(win["title"])
        if niri_window:
            storage.store("librewolf", uuid, niri_window.id)

    matcher.save()
```

### Layer 2c: Restore Orchestration

**tmux restore** (`src/wlrenv/niri/restore.py`):

```python
def restore_tmux() -> None:
    sessions = get_detached_tmux_sessions()

    for session in sessions:
        props = storage.lookup("tmux", session)
        proc = spawn_terminal(["tmux", "attach-session", "-t", session])

        if props:
            window_id = niri.wait_for_window(pid=proc.pid)
            niri.configure(window_id, **props)
```

**mosh restore:**

```python
def restore_mosh() -> None:
    sessions = read_moshen_sessions()

    for host, session_name in sessions:
        identity = f"{host}:{session_name}"
        props = storage.lookup("mosh", identity)
        proc = spawn_terminal(["moshen", host, session_name])

        if props:
            window_id = niri.wait_for_window(pid=proc.pid)
            niri.configure(window_id, **props)
```

**Librewolf restore** (in native messaging host):

```python
def handle_restore_message(windows: list[dict]) -> dict:
    windows = sorted(windows, key=lambda w: len(w["urls"]), reverse=True)

    matcher = UrlMatcher.load()
    moved_count = 0

    for win in windows:
        uuid = matcher.match_or_create(win["urls"])
        props = storage.lookup("librewolf", uuid)
        niri_window = niri.find_window_by_title(win["title"])

        if niri_window and props:
            niri.configure(niri_window.id, **props)
            moved_count += 1

    matcher.save()
    return {"success": True, "moved_count": moved_count}
```

## Module Structure

```
src/wlrenv/niri/
  __init__.py
  ipc.py             # niri msg wrapper
  storage.py         # JSON read/write with atomic saves
  identify.py        # tmux/mosh child process parsing
  librewolf.py       # URL matching, UUID assignment
  track.py           # unified terminal tracker
  restore.py         # tmux/mosh restore logic
  cli.py             # entry point functions
```

## Entry Points

In `pyproject.toml`:

```toml
[project.scripts]
xonsh = 'xonsh.main:main'
wlr-niri-track-terminals = 'wlrenv.niri.cli:track_terminals'
wlr-niri-restore-tmux = 'wlrenv.niri.cli:restore_tmux'
wlr-niri-restore-mosh = 'wlrenv.niri.cli:restore_mosh'
wlr-niri-librewolf-host = 'wlrenv.niri.cli:librewolf_native_host'
```

## Scripts Replaced

| Old (bash) | New (Python) | Notes |
|------------|--------------|-------|
| `wlr-tmux-niri-tracker` | `wlr-niri-track-terminals` | Combined with mosh |
| `wlr-open-tmux-sessions` | `wlr-niri-restore-tmux` | |
| `wlr-restore-moshen-sessions` | `wlr-niri-restore-mosh` | |
| `wlr-niri-configure-window` | `niri.configure()` | Absorbed into module |
| `wlr-tmux-resurrect-save-workspaces` | (removed) | No longer needed |
| `wlr-tmux-resurrect-restore-workspaces` | (removed) | No longer needed |

## Systemd Integration

Update existing timers to call new entry points:
- `wlr-tmux-niri-tracker.timer` → runs `wlr-niri-track-terminals`
- `wlr-librewolf-niri-tracker.timer` → (unchanged, still uses extension)

## Migration

No data migration needed:
- New tracker will populate storage from currently-running windows
- Librewolf JSON can be moved to new location or regenerated

## Design Decisions

1. **Python over bash**: Complex logic (URL matching, JSON manipulation) is cleaner in Python
2. **Separate files per app**: Isolation for debugging, reduced blast radius on corruption
3. **UUID for Librewolf**: Stable identity via URL matching, decoupled from ephemeral window titles
4. **Greedy URL matching**: Process windows by URL count descending, assign best match
5. **Atomic writes**: Tempfile + rename to prevent corruption
6. **No tmux option storage**: External JSON is more reliable than piggybacking on resurrect

## Future Extensions

- Add parsers for zellij, k9s, other terminal apps
- Track column ordering (not just workspace/width)
- Cross-monitor considerations if multiple displays
