# cc-openai: Claude Code variant backed by a local pi-ai OpenAI Codex proxy.
#
# The wrapper starts cc-openai-proxy if the configured local endpoint is not
# healthy, then runs the OpenAI-branded patched Claude Code binary against that
# Anthropic-compatible endpoint. ChatGPT plan credentials are read by the proxy
# from pi's ~/.pi/agent/auth.json (or CC_OPENAI_AUTH_FILE).
{
  lib,
  writeShellScriptBin,
  writeText,
  curl,
  coreutils,
  cc-openai-proxy,
  claude-code-bin,
}:

let
  command = "cc-openai";
  themeSlug = "openai-slate";
  brandSettings = writeText "${command}-brand-settings.json" (
    builtins.toJSON {
      theme = "custom:${themeSlug}";
    }
  );
  wrapper = writeShellScriptBin command ''
    set -euo pipefail

    export CLAUDE_CONFIG_DIR="''${CC_OPENAI_CONFIG_DIR:-$HOME/.${command}}"

    host="''${CC_OPENAI_PROXY_HOST:-127.0.0.1}"
    port="''${CC_OPENAI_PROXY_PORT:-17780}"
    default_base_url="http://$host:$port"
    base_url="''${CC_OPENAI_PROXY_URL:-$default_base_url}"
    autostart="''${CC_OPENAI_PROXY_AUTOSTART:-1}"
    health_url="$base_url/health"

    is_healthy() {
      ${curl}/bin/curl -fsS --max-time 1 "$health_url" >/dev/null 2>&1
    }

    if [ "$autostart" = "1" ] && [ -z "''${CC_OPENAI_PROXY_URL:-}" ] && ! is_healthy; then
      log="''${CC_OPENAI_PROXY_LOG:-''${XDG_STATE_HOME:-$HOME/.local/state}/cc-openai/proxy.log}"
      log_dir="''${log%/*}"
      if [ "$log_dir" != "$log" ]; then
        ${coreutils}/bin/mkdir -p "$log_dir"
      fi
      ${coreutils}/bin/nohup ${cc-openai-proxy}/bin/cc-openai-proxy --host "$host" --port "$port" >> "$log" 2>&1 &

      for _ in {1..50}; do
        if is_healthy; then
          break
        fi
        sleep 0.1
      done

      if ! is_healthy; then
        echo "${command}: proxy did not become healthy at $health_url" >&2
        echo "${command}: see $log" >&2
        exit 1
      fi
    elif ! is_healthy; then
      echo "${command}: proxy is not healthy at $health_url" >&2
      echo "${command}: set CC_OPENAI_PROXY_AUTOSTART=1 or start cc-openai-proxy manually" >&2
      exit 1
    fi

    default_model="''${CC_OPENAI_DEFAULT_MODEL:-gpt-5.5}"
    haiku_model="''${CC_OPENAI_HAIKU_MODEL:-gpt-5.4-mini}"

    export ANTHROPIC_BASE_URL="$base_url"
    export ANTHROPIC_AUTH_TOKEN="''${ANTHROPIC_AUTH_TOKEN:-cc-openai-local}"
    unset ANTHROPIC_API_KEY

    export ANTHROPIC_MODEL="''${CC_OPENAI_MODEL:-$default_model}"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="''${CC_OPENAI_OPUS_MODEL:-''${CC_OPENAI_MODEL:-$default_model}}"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="''${CC_OPENAI_SONNET_MODEL:-''${CC_OPENAI_MODEL:-$default_model}}"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="''${CC_OPENAI_MODEL:-$haiku_model}"
    export ANTHROPIC_SMALL_FAST_MODEL="''${CC_OPENAI_MODEL:-$haiku_model}"
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
