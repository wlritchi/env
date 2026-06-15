# Shared launcher for cc-mirror-style provider variants (cc-kimi, cc-zai, ...).
#
# Given a brand-patched claude-code binary and provider data, it isolates a
# config dir, exports the provider env, and execs the binary. Branding that used
# to live here now lives in the artifacts: the splash and onboarding skip are
# baked into the binary (see ccpatch brands), and the theme + blocked-tools live
# in Nix-managed config files exposed via `passthru.homeFiles` (wire them into
# home.file). The wrapper itself writes nothing. Auth is the user's to provide
# via ANTHROPIC_AUTH_TOKEN (ANTHROPIC_API_KEY is cleared).
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
  deny ? [ ], # tool names to block via settings.json permissions.deny
}:

let
  configDirVar = lib.toUpper (builtins.replaceStrings [ "-" ] [ "_" ] command) + "_CONFIG_DIR";

  settings = writeText "${command}-settings.json" (
    builtins.toJSON (
      {
        theme = "custom:${themeSlug}";
      }
      // lib.optionalAttrs (deny != [ ]) { permissions = { inherit deny; }; }
    )
  );
  envExports = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (k: v: "export ${k}=${lib.escapeShellArg v}") env
  );

  wrapper = writeShellScriptBin command ''
    set -euo pipefail

    # Isolated config dir so variant state never mixes with `claude`. The
    # Nix-managed config files (theme + settings) are installed at the *default*
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

    exec ${binary}/libexec/claude-code/claude "$@"
  '';
in
# The wrapper derivation, plus the home-manager files the variant needs. Theme
# and settings (theme selection + blocked tools) are read-only store symlinks
# refreshed by home-manager, replacing the old per-launch wrapper writes. A
# `/theme` change at runtime therefore won't persist -- the brand theme is
# pinned, by design. The config dir hosting them is the wrapper's default.
wrapper
// {
  homeFiles = {
    ".${command}/themes/${themeSlug}.json".source = themeFile;
    ".${command}/settings.json".source = settings;
  };
}
