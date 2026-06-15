# cc-kimi: Kimi Code variant of Claude Code.
#
# Uses a Kimi-branded build of the patched claude-code binary (startup label
# "Kimi Code"; theme/thinking-verbs are future tier-2 patches) pointed at Kimi's
# coding API with the kimi-for-coding model mapping and an isolated config dir,
# and shows the Kimi splash on interactive launch.
#
# Auth is out of scope: provide the Kimi token via ANTHROPIC_AUTH_TOKEN in your
# environment (e.g. from your secret manager). The wrapper clears
# ANTHROPIC_API_KEY so Claude Code authenticates with the token.
#
# Config dir defaults to ~/.cc-kimi (override with CC_KIMI_CONFIG_DIR); kept
# separate from `claude` so onboarding/history/projects never mix.
{
  writeShellScriptBin,
  writeText,
  claude-code-bin,
}:
let
  kimiSplash = ./cc-kimi-splash.txt;
  # Seed onboarding so a fresh config dir doesn't trigger the first-run wizard.
  seedClaudeJson = writeText "cc-kimi-claude.json" (
    builtins.toJSON {
      hasCompletedOnboarding = true;
      theme = "dark";
    }
  );
in
writeShellScriptBin "cc-kimi" ''
  set -euo pipefail

  # Isolated config dir so cc-kimi state never mixes with `claude`.
  export CLAUDE_CONFIG_DIR="''${CC_KIMI_CONFIG_DIR:-$HOME/.cc-kimi}"
  mkdir -p "$CLAUDE_CONFIG_DIR"
  [ -e "$CLAUDE_CONFIG_DIR/.claude.json" ] \
    || install -m600 ${seedClaudeJson} "$CLAUDE_CONFIG_DIR/.claude.json"

  # Kimi coding endpoint + model mapping (everything routes to kimi-for-coding).
  export ANTHROPIC_BASE_URL="https://api.kimi.com/coding"
  export ANTHROPIC_CUSTOM_HEADERS="User-Agent: KimiCLI/1.5"
  export ANTHROPIC_MODEL="kimi-for-coding"
  export ANTHROPIC_DEFAULT_HAIKU_MODEL="kimi-for-coding"
  export ANTHROPIC_DEFAULT_SONNET_MODEL="kimi-for-coding"
  export ANTHROPIC_DEFAULT_OPUS_MODEL="kimi-for-coding"
  export ANTHROPIC_SMALL_FAST_MODEL="kimi-for-coding"
  export CLAUDE_CODE_SUBAGENT_MODEL="kimi-for-coding"
  export API_TIMEOUT_MS="3000000"
  export BASH_DEFAULT_TIMEOUT_MS="3600000"
  export DISABLE_INSTALLATION_CHECKS=1
  export DISABLE_AUTOUPDATER=1
  export DISABLE_AUTO_MIGRATE_TO_NATIVE=1

  # Tag telemetry with the Kimi backend (the rest of the OTEL config -- exporter,
  # endpoint, enable flag -- is inherited from your environment).
  export OTEL_RESOURCE_ATTRIBUTES="model.backend=kimi"

  # Kimi authenticates with ANTHROPIC_AUTH_TOKEN (not an API key). Clear the API
  # key path so Claude Code uses the token; the token itself is yours to provide.
  unset ANTHROPIC_API_KEY
  if [ -z "''${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "cc-kimi: ANTHROPIC_AUTH_TOKEN is unset; Kimi requests will fail until you set it." >&2
  fi

  # Kimi splash on interactive launch (skip for print / non-interactive modes).
  __cc_kimi_splash=1
  for __a in "$@"; do
    case "$__a" in
      -p | --print | --output-format) __cc_kimi_splash=0 ;;
    esac
  done
  if [ "$__cc_kimi_splash" = 1 ] && [ -t 1 ]; then
    cat ${kimiSplash}
    printf '\n'
  fi

  exec ${claude-code-bin}/libexec/claude-code/claude "$@"
''
