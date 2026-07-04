# Shared launcher for cc-mirror-style provider variants (cc-kimi, cc-zai, ...).
#
# Given a brand-patched claude-code binary and provider data, it isolates a
# config dir, exports the provider env, layers the brand overrides on top of your
# base settings, and execs the binary. Branding lives in the artifacts: the
# splash and onboarding skip are baked into the binary (see ccpatch brands); the
# theme *definition* ships as a `home.file` (passthru.homeFiles); and the brand
# *overrides* (theme selection + blocked tools) ride the `--settings` flag layer
# so they override/extend -- never replace -- the inherited base settings.
#
# Settings inheritance: the variant's `settings.json` (userSettings layer) is a
# symlink to your base `~/.claude/settings.json`, wired in common.nix (it needs
# `config.lib.file.mkOutOfStoreSymlink`, unavailable here). The brand overrides
# go through `--settings`: flagSettings overrides scalars like `theme` and
# *concatenates* `permissions.deny`, and is never written back -- so Claude's
# runtime pref write-backs only ever touch genuinely shareable keys in the base.
#
# This is the curried form: callPackage fills the nixpkgs args, leaving a
# function of the per-provider attrs below.
{
  lib,
  writeShellScriptBin,
  writeText,
}:

{
  command, # e.g. "cc-kimi"; config dir defaults to ~/.<command>, override via <COMMAND>_CONFIG_DIR
  binary, # brand-patched claude-code derivation (uses its libexec binary)
  env, # attrset of provider env vars (string values)
  themeFile, # path to a {name,base,overrides} custom-theme JSON
  themeSlug, # theme file is installed as <slug>.json and selected as custom:<slug>
  deny ? [ ], # tool names to block via permissions.deny (concatenated onto base)
}:

let
  configDirVar = lib.toUpper (builtins.replaceStrings [ "-" ] [ "_" ] command) + "_CONFIG_DIR";

  # Brand overrides injected via --settings (flagSettings layer): forces the
  # brand theme and adds the blocked tools, on top of the inherited base.
  #
  # apiKeyHelper = "" pins the helper OFF for the variant, independent of the
  # inherited base. The variants authenticate purely via ANTHROPIC_AUTH_TOKEN
  # (bearer); a configured apiKeyHelper would UNCONDITIONALLY shadow that path,
  # and if it ever produced no output it caches a single-space key that breaks
  # auth. Since flagSettings is folded over userSettings (later-wins) and the
  # empty string is a defined value that overrides in the lodash mergeWith, this
  # neutralizes any apiKeyHelper someone later adds to ~/.claude/settings.json --
  # xN() gates on truthiness, so "" reads as "no helper". Must be "" not null:
  # the setting is a zod .string().optional(), so null fails invalid_type.
  brandSettings = writeText "${command}-brand-settings.json" (
    builtins.toJSON (
      {
        theme = "custom:${themeSlug}";
        apiKeyHelper = "";
      }
      // lib.optionalAttrs (deny != [ ]) { permissions = { inherit deny; }; }
    )
  );
  envExports = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (k: v: "export ${k}=${lib.escapeShellArg v}") env
  );

  wrapper = writeShellScriptBin command ''
    set -euo pipefail

    # Isolated config dir so variant runtime state (.claude.json) never mixes with
    # `claude`. The theme def + the settings->base symlink land at the *default*
    # ~/.${command}; if you override ${configDirVar} you own populating that dir.
    export CLAUDE_CONFIG_DIR="''${${configDirVar}:-$HOME/.${command}}"

    # Provider endpoint, model mapping, and operational env.
    ${envExports}

    # These providers authenticate with ANTHROPIC_AUTH_TOKEN (not an API key).
    # Clear the API-key path so the token is used; the token is yours to provide.
    unset ANTHROPIC_API_KEY
    if [ -z "''${ANTHROPIC_AUTH_TOKEN:-}" ]; then
      echo "${command}: ANTHROPIC_AUTH_TOKEN is unset; requests will fail until you set it." >&2
    fi

    # --settings first so a user-supplied --settings later in argv still wins.
    exec ${binary}/libexec/claude-code/claude --settings ${brandSettings} "$@"
  '';
in
# The wrapper derivation, plus the theme-definition home.file. `command` is
# exposed so common.nix can wire the settings->base symlink (which needs the
# home-manager `config`, unavailable in this pure builder).
wrapper
// {
  inherit command;
  homeFiles = {
    ".${command}/themes/${themeSlug}.json".source = themeFile;
  };
}
