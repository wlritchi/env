# cc-openai: Claude Code variant backed by a local pi-ai OpenAI Codex proxy.
#
# By default, the wrapper requests startup of the platform proxy service when
# the local endpoint is not healthy. It does not request startup when autostart
# is disabled or CC_OPENAI_PROXY_URL is set. The proxy reads ChatGPT plan
# credentials from CC_OPENAI_AUTH_FILE, PI_AUTH_FILE, or ~/.pi/agent/auth.json.
{
  lib,
  writeShellScriptBin,
  writeText,
  curl,
  coreutils,
  claude-code-bin,
}:

let
  command = "cc-openai";
  themeSlug = "openai-slate";
  # apiKeyHelper = "" pins the helper OFF, independent of the inherited base
  # settings, so a helper later added to ~/.claude/settings.json can't shadow the
  # ANTHROPIC_AUTH_TOKEN bearer path (see claude-code-variant.nix for the trace).
  brandSettings = writeText "${command}-brand-settings.json" (
    builtins.toJSON {
      theme = "custom:${themeSlug}";
      apiKeyHelper = "";
    }
  );
  wrapper = writeShellScriptBin command ''
    set -euo pipefail

    export CLAUDE_CONFIG_DIR="''${CC_OPENAI_CONFIG_DIR:-$HOME/.${command}}"

    default_base_url="http://127.0.0.1:17780"
    base_url="''${CC_OPENAI_PROXY_URL:-$default_base_url}"
    autostart="''${CC_OPENAI_PROXY_AUTOSTART:-1}"
    health_url="$base_url/health"

    is_healthy() {
      ${curl}/bin/curl -fsS --max-time 1 "$health_url" >/dev/null 2>&1
    }

    start_managed_proxy() {
      case "$(${coreutils}/bin/uname -s)" in
        Linux)
          if ! command -v systemctl >/dev/null 2>&1; then
            echo "${command}: systemctl is unavailable" >&2
            return 1
          fi
          systemctl --user start cc-openai-proxy.service
          ;;
        Darwin)
          /bin/launchctl kickstart \
            "gui/$(${coreutils}/bin/id -u)/org.nix-community.home.cc-openai-proxy"
          ;;
        *)
          echo "${command}: unsupported service manager" >&2
          return 1
          ;;
      esac
    }

    if [ "$autostart" = "1" ] && [ -z "''${CC_OPENAI_PROXY_URL:-}" ] && ! is_healthy; then
      start_managed_proxy
      for _ in {1..50}; do
        if is_healthy; then
          break
        fi
        sleep 0.1
      done

      if ! is_healthy; then
        echo "${command}: managed proxy did not become healthy at $health_url" >&2
        echo "${command}: inspect the cc-openai-proxy user service logs" >&2
        exit 1
      fi
    elif ! is_healthy; then
      echo "${command}: proxy is not healthy at $health_url" >&2
      echo "${command}: set CC_OPENAI_PROXY_AUTOSTART=1 or start the configured proxy" >&2
      exit 1
    fi

    default_model="''${CC_OPENAI_DEFAULT_MODEL:-gpt-5.6-sol}"
    sonnet_model="''${CC_OPENAI_SONNET_MODEL:-''${CC_OPENAI_MODEL:-gpt-5.6-terra}}"
    haiku_model="''${CC_OPENAI_HAIKU_MODEL:-''${CC_OPENAI_MODEL:-gpt-5.6-luna}}"

    export ANTHROPIC_BASE_URL="$base_url"
    export ANTHROPIC_AUTH_TOKEN="''${ANTHROPIC_AUTH_TOKEN:-cc-openai-local}"
    unset ANTHROPIC_API_KEY

    export ANTHROPIC_MODEL="''${CC_OPENAI_MODEL:-$default_model}"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="''${CC_OPENAI_OPUS_MODEL:-''${CC_OPENAI_MODEL:-$default_model}}"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="$sonnet_model"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="$haiku_model"
    export ANTHROPIC_DEFAULT_FABLE_MODEL="''${CC_OPENAI_FABLE_MODEL:-''${CC_OPENAI_MODEL:-$default_model}}"
    export ANTHROPIC_SMALL_FAST_MODEL="$haiku_model"
    export CLAUDE_CODE_SUBAGENT_MODEL="''${CC_OPENAI_MODEL:-$default_model}"
    export API_TIMEOUT_MS="''${API_TIMEOUT_MS:-3000000}"
    export BASH_DEFAULT_TIMEOUT_MS="''${BASH_DEFAULT_TIMEOUT_MS:-3600000}"
    export DISABLE_INSTALLATION_CHECKS=1
    export DISABLE_AUTOUPDATER=1
    export DISABLE_AUTO_MIGRATE_TO_NATIVE=1
    export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="''${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-1}"
    export OTEL_RESOURCE_ATTRIBUTES="''${OTEL_RESOURCE_ATTRIBUTES:-model.backend=openai-codex}"

    exec ${claude-code-bin}/libexec/claude-code/claude --settings ${brandSettings} "$@"
  '';
in
wrapper
// {
  inherit command;
  homeFiles = {
    ".${command}/themes/${themeSlug}.json".source = ./openai-slate-theme.json;
  };
}
