# Catppuccin Macchiato theme for Claude Code

**Date:** 2026-05-29
**Status:** Approved (design)

## Goal

Give Claude Code's TUI a faithful Catppuccin Macchiato palette, syncing across
all machines via the dotfiles repo. First cut covers the **UI palette** (chrome,
diffs, semantic colors, backgrounds). Recoloring the **syntax-highlight token
colors** inside code/diffs is an explicit follow-up (different mechanism).

## Background

Claude Code (2.1.x) ships a built-in custom-theme system. At startup it reads
`~/.claude/themes/<slug>.json` files of the form:

```json
{ "name": "...", "base": "dark", "overrides": { "<paletteKey>": "<color>" } }
```

The loader keeps only `overrides` keys that exist in the chosen `base` palette
and whose value passes the color validator (accepts `#rrggbb`, `#rgb`,
`rgb(r,g,b)`, `ansi256(n)`, `ansi:<name>`). A theme is selected by setting the
config key `theme` to `"custom:<slug>"`. `theme` is a recognized `settings.json`
key (`union([enum(builtins), string().startsWith("custom:")])`), so activation
can be committed declaratively.

The dark base palette has **69 keys**; we override all of them so nothing falls
back to stock dark.

## Approach

**Config-based custom theme — no binary patching.** Rejected alternatives:

- *Binary patch of the dark palette literals*: the `rgb(...)` strings are
  length-constrained (byte-length must be preserved in the Bun standalone
  binary) and version-fragile. The config system is the supported path.
- *Shim-generated theme file at launch*: unnecessary — the dotfiles sync already
  symlinks files into `~/.claude/` (it `mkdir -p`s parent dirs), so a committed
  file under `dotfiles/.claude/themes/` is sufficient.

## Changes

1. **New file** `dotfiles/.claude/themes/catppuccin-macchiato.json`
   → synced to `~/.claude/themes/catppuccin-macchiato.json`. Contents: the
   `{name, base:"dark", overrides:{…69…}}` document below.
2. **Edit** `dotfiles/.claude/settings.json` — add top-level
   `"theme": "custom:catppuccin-macchiato"`.

## Activation & precedence

`settings.json.theme` accepts the `custom:` slug, so setting it there activates
the theme everywhere the dotfiles sync. If a machine has a prior `/theme`
selection persisted in the (unsynced) global config that shadows the
settings.json value, the fallback is a one-time `/theme` pick on that machine.
**To confirm during implementation:** whether settings.json `theme` wins over a
pre-existing global-config selection. If it does not, document the one-time
`/theme` step (the theme *file* still syncs everywhere).

## Color mapping (Catppuccin Macchiato)

Reference palette (Macchiato): Rosewater `#f4dbd6`, Flamingo `#f0c6c6`, Pink
`#f5bde6`, Mauve `#c6a0f6`, Red `#ed8796`, Maroon `#ee99a0`, Peach `#f5a97f`,
Yellow `#eed49f`, Green `#a6da95`, Teal `#8bd5ca`, Sky `#91d7e3`, Sapphire
`#7dc4e4`, Blue `#8aadf4`, Lavender `#b7bdf8`, Text `#cad3f5`, Subtext1/0
`#b8c0e0`/`#a5adcb`, Overlay2/1/0 `#939ab7`/`#8087a2`/`#6e738d`, Surface2/1/0
`#5b6078`/`#494d64`/`#363a4f`, Base `#24273a`, Mantle `#1e2030`, Crust `#181926`.

Derivation rules:
- **Diff backgrounds** are derived in the green/red hue at low lightness so light
  `text` (`#cad3f5`) stays readable: `dimmed` ≈ L 0.20, line ≈ L 0.26, `word` ≈
  L 0.40 (saturation trimmed). This is the fix for the original
  light-on-light/256-downsample problem.
- **`*Shimmer`** keys = their base accent lightened ~8–10% (keeps hue, preserves
  the animated pulse).
- Everything else maps to the nearest semantic Macchiato accent.

### Full theme document

```json
{
  "name": "Catppuccin Macchiato",
  "base": "dark",
  "overrides": {
    "claude": "#f5a97f",
    "claudeShimmer": "#f9c9ae",
    "clawd_body": "#f5a97f",
    "briefLabelClaude": "#f5a97f",
    "claudeBlue_FOR_SYSTEM_SPINNER": "#b7bdf8",
    "claudeBlueShimmer_FOR_SYSTEM_SPINNER": "#dcdffc",
    "autoAccept": "#c6a0f6",
    "effortUltra": "#c6a0f6",
    "merged": "#c6a0f6",
    "permission": "#b7bdf8",
    "permissionShimmer": "#dcdffc",
    "suggestion": "#b7bdf8",
    "remember": "#b7bdf8",
    "rate_limit_fill": "#b7bdf8",
    "bashBorder": "#f5bde6",
    "planMode": "#8bd5ca",
    "background": "#8bd5ca",
    "ide": "#8aadf4",
    "professionalBlue": "#8aadf4",
    "briefLabelYou": "#7dc4e4",
    "text": "#cad3f5",
    "inverseText": "#24273a",
    "inactive": "#8087a2",
    "inactiveShimmer": "#939ab7",
    "subtle": "#5b6078",
    "promptBorder": "#6e738d",
    "promptBorderShimmer": "#8087a2",
    "success": "#a6da95",
    "error": "#ed8796",
    "warning": "#eed49f",
    "warningShimmer": "#f4e4c2",
    "chromeYellow": "#eed49f",
    "fastMode": "#f5a97f",
    "fastModeShimmer": "#f9c9ae",
    "diffAdded": "#38572e",
    "diffAddedDimmed": "#2c4125",
    "diffAddedWord": "#558844",
    "diffRemoved": "#62222c",
    "diffRemovedDimmed": "#481e24",
    "diffRemovedWord": "#9b3141",
    "red_FOR_SUBAGENTS_ONLY": "#ed8796",
    "blue_FOR_SUBAGENTS_ONLY": "#8aadf4",
    "green_FOR_SUBAGENTS_ONLY": "#a6da95",
    "yellow_FOR_SUBAGENTS_ONLY": "#eed49f",
    "purple_FOR_SUBAGENTS_ONLY": "#c6a0f6",
    "orange_FOR_SUBAGENTS_ONLY": "#f5a97f",
    "pink_FOR_SUBAGENTS_ONLY": "#f5bde6",
    "cyan_FOR_SUBAGENTS_ONLY": "#91d7e3",
    "userMessageBackground": "#363a4f",
    "userMessageBackgroundHover": "#494d64",
    "bashMessageBackgroundColor": "#363a4f",
    "memoryBackgroundColor": "#363a4f",
    "selectionBg": "#494d64",
    "clawd_background": "#181926",
    "rate_limit_empty": "#494d64",
    "rainbow_red": "#ed8796",
    "rainbow_orange": "#f5a97f",
    "rainbow_yellow": "#eed49f",
    "rainbow_green": "#a6da95",
    "rainbow_blue": "#8aadf4",
    "rainbow_indigo": "#b7bdf8",
    "rainbow_violet": "#c6a0f6",
    "rainbow_red_shimmer": "#f2aab5",
    "rainbow_orange_shimmer": "#f8c2a5",
    "rainbow_yellow_shimmer": "#f4e4c2",
    "rainbow_green_shimmer": "#bfe5b3",
    "rainbow_blue_shimmer": "#afc7f8",
    "rainbow_indigo_shimmer": "#dcdffc",
    "rainbow_violet_shimmer": "#dcc5fa"
  }
}
```

## Out of scope (follow-up)

- **Syntax-highlight token colors** inside code/diffs (the hardcoded
  "Monokai Extended" scope map). Not reachable via the theme `overrides` system;
  would require a binary patch of the scope map or a syntax-theme mechanism.

## Verification

1. `catppuccin-macchiato.json` is valid JSON and is discovered as a custom theme
   (slug `catppuccin-macchiato`).
2. Every override key exists in the dark base palette (all 69 should apply; none
   silently dropped).
3. With the theme active, a PTY capture of the TUI shows Macchiato values in the
   emitted escapes (e.g. `text` → `38;2;202;211;245`), confirming the overrides
   take effect in true color.
4. Spot-check a diff: added lines on `#38572e` / removed on `#62222c`, readable.
