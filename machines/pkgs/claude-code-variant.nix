# Shared launcher for cc-mirror-style provider variants (cc-kimi, cc-zai, ...).
#
# Given a brand-patched claude-code binary and provider data, it isolates a
# config dir, ships + selects a custom theme, blocks tools, exports the provider
# env, shows a splash on interactive launch, and execs the binary. Auth is the
# user's to provide via ANTHROPIC_AUTH_TOKEN (ANTHROPIC_API_KEY is cleared).
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
  splash ? null, # path to splash art shown on interactive launch, or null
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
  seedClaudeJson = writeText "${command}-claude.json" (
    builtins.toJSON { hasCompletedOnboarding = true; }
  );
  envExports = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (k: v: "export ${k}=${lib.escapeShellArg v}") env
  );
  splashBlock = lib.optionalString (splash != null) ''
    __splash=1
    for __a in "$@"; do
      case "$__a" in
        -p | --print | --output-format) __splash=0 ;;
      esac
    done
    if [ "$__splash" = 1 ] && [ -t 1 ]; then
      cat ${splash}
      printf '\n'
    fi
  '';
in
writeShellScriptBin command ''
  set -euo pipefail

  # Isolated config dir so variant state never mixes with `claude`.
  export CLAUDE_CONFIG_DIR="''${${configDirVar}:-$HOME/.${command}}"
  mkdir -p "$CLAUDE_CONFIG_DIR/themes"
  [ -e "$CLAUDE_CONFIG_DIR/.claude.json" ] \
    || install -m600 ${seedClaudeJson} "$CLAUDE_CONFIG_DIR/.claude.json"

  # Ship the theme and select it (+ any blocked tools) via managed settings,
  # both refreshed every launch so updates propagate and a stale .claude.json
  # can't shadow the selection.
  install -m644 ${themeFile} "$CLAUDE_CONFIG_DIR/themes/${themeSlug}.json"
  install -m644 ${settings} "$CLAUDE_CONFIG_DIR/settings.json"

  # Provider endpoint, model mapping, and operational env.
  ${envExports}

  # These providers authenticate with ANTHROPIC_AUTH_TOKEN (not an API key).
  # Clear the API-key path so the token is used; the token is yours to provide.
  unset ANTHROPIC_API_KEY
  if [ -z "''${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "${command}: ANTHROPIC_AUTH_TOKEN is unset; requests will fail until you set it." >&2
  fi

  ${splashBlock}
  exec ${binary}/libexec/claude-code/claude "$@"
''
