# WINDOWNAV tmux Keybindings Design

## Problem

The WINDOWNAV keyboard layer encodes pane-scope operations (navigate, move, resize,
consume/emit, one-shot) and tab operations using F13-F24 with modifier combinations.
These need to reach tmux, but tmux can't bind F13-F24 or XF86 keysyms directly. On
Linux, XKB remaps F13-F24 to XF86* keysyms, and terminal emulators can't bind most
of those either.

## Constraints

- Multi-platform: Linux and macOS
- Multi-WM: niri, xmonad, Aerospace
- Multi-terminal: Alacritty and Ghostty
- Must work over SSH/mosh (WM-level `spawn tmux` is not viable)
- Ghostty on Linux cannot bind F13-F24 (no scancode binding support)

## Architecture

Terminal emulator keybindings translate key events into custom escape sequences.
tmux binds those escape sequences via `user-keys`.

```
Keyboard (HID F13-F24 + modifiers)
  -> OS (XKB remaps to XF86* on Linux, passthrough on macOS)
  -> WM (passes pane-scope/tab keys through to focused app)
  -> Terminal emulator (translates to custom escape sequence)
  -> tmux (matches user-keys, executes bound command)
```

### Platform-specific terminal bindings

- **Alacritty Linux**: Bind by evdev scancode (183-194 for F13-F24)
- **Alacritty macOS**: Bind by F-key name ("F13"-"F24")
- **Ghostty macOS**: Bind by F-key name (f13-f24)
- **Ghostty Linux**: Not supported (deferred until Ghostty adds scancode binding)

Both scancode and F-key name bindings go in the base config files. On each platform,
only the matching bindings fire; the others are harmlessly ignored.

## Escape Sequence Convention

CSI sequences `\e[200~` through `\e[226~`, well outside any standard key encoding
(standard F-keys only go up to `\e[34~`).

### Pane-scope directional (12 sequences)

| User-key | Escape seq | Operation | Keyboard keybind |
|----------|------------|-----------|------------------|
| User0 | `\e[200~` | Navigate pane left | Super+F13 |
| User1 | `\e[201~` | Navigate pane up | Super+F14 |
| User2 | `\e[202~` | Navigate pane down | Super+F15 |
| User3 | `\e[203~` | Navigate pane right | Super+F16 |
| User4 | `\e[204~` | Move pane left | Super+Shift+F13 |
| User5 | `\e[205~` | Move pane up | Super+Shift+F14 |
| User6 | `\e[206~` | Move pane down | Super+Shift+F15 |
| User7 | `\e[207~` | Move pane right | Super+Shift+F16 |
| User8 | `\e[208~` | Resize pane left | Super+Ctrl+F13 |
| User9 | `\e[209~` | Resize pane up | Super+Ctrl+F14 |
| User10 | `\e[210~` | Resize pane down | Super+Ctrl+F15 |
| User11 | `\e[211~` | Resize pane right | Super+Ctrl+F16 |

### Pane-scope consume/emit (8 sequences)

| User-key | Escape seq | Operation | Keyboard keybind |
|----------|------------|-----------|------------------|
| User12 | `\e[212~` | Consume pane from left | Super+F17 |
| User13 | `\e[213~` | Consume pane from up | Super+F18 |
| User14 | `\e[214~` | Consume pane from down | Super+F19 |
| User15 | `\e[215~` | Consume pane from right | Super+F20 |
| User16 | `\e[216~` | Emit pane left | Super+F21 |
| User17 | `\e[217~` | Emit pane up | Super+F22 |
| User18 | `\e[218~` | Emit pane down | Super+F23 |
| User19 | `\e[219~` | Emit pane right | Super+F24 |

### Pane-scope one-shot (3 sequences)

| User-key | Escape seq | Operation | Keyboard keybind |
|----------|------------|-----------|------------------|
| User20 | `\e[220~` | Zoom pane | Ctrl+Shift+Super+F |
| User21 | `\e[221~` | Close pane | Ctrl+Shift+Super+X |
| User22 | `\e[222~` | Create pane (vsplit) | Ctrl+Shift+Super+A |

### Tab operations (4 sequences)

| User-key | Escape seq | Operation | Keyboard keybind |
|----------|------------|-----------|------------------|
| User23 | `\e[223~` | Tab navigate left | Ctrl+Alt+F13 |
| User24 | `\e[224~` | Tab navigate right | Ctrl+Alt+F16 |
| User25 | `\e[225~` | Tab move left | Ctrl+Alt+Shift+F13 |
| User26 | `\e[226~` | Tab move right | Ctrl+Alt+Shift+F16 |

## tmux Command Mapping

| Operation | tmux command |
|-----------|-------------|
| Navigate pane L/U/D/R | `select-pane -L/-U/-D/-R` |
| Move pane L/U/D/R | `select-pane -L/-U/-D/-R \; swap-pane -t !` |
| Resize pane L/U/D/R | `resize-pane -L/-U/-D/-R 5` |
| Consume from L/R | `join-pane -h -s !` |
| Consume from U/D | `join-pane -v -s !` |
| Emit (all directions) | `break-pane -d` |
| Zoom pane | `resize-pane -Z` |
| Close pane | `kill-pane` |
| Create pane | `split-window -h` |
| Tab navigate L/R | `select-window -t -1/+1` |
| Tab move L/R | `swap-window -t -1/+1 \; select-window -t -1/+1` |

## Files to Modify

1. `dotfiles/.tmux.conf` — user-keys + bind commands
2. `dotfiles/.config/alacritty/alacritty.toml` — scancode + F-key name bindings
3. `dotfiles/.config/ghostty/config` — F-key name bindings (macOS only in practice)
4. `docs/windownav-host-keybinds.md` — document escape sequence convention

## Out of Scope

- XKB customization (not needed with Alacritty scancode binding)
- Ghostty Linux support (deferred)
- Keyboard firmware changes (current encoding is correct)
- niri config changes (already handles window/workspace/monitor scopes)
