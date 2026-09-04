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
  cc-openai-proxy-launcher,
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

    source ${cc-openai-proxy-launcher}
    cc_openai_proxy_configure required ${command} || exit 1

    selected_model="$(cc_openai_qualify_model "''${CC_OPENAI_MODEL:-''${CC_OPENAI_DEFAULT_MODEL:-gpt-5.6-sol}}")"
    sonnet_model="$(cc_openai_qualify_model "''${CC_OPENAI_SONNET_MODEL:-''${CC_OPENAI_MODEL:-gpt-5.6-terra}}")"
    haiku_model="$(cc_openai_qualify_model "''${CC_OPENAI_HAIKU_MODEL:-''${CC_OPENAI_MODEL:-gpt-5.6-luna}}")"

    export ANTHROPIC_BASE_URL="$CC_OPENAI_PROXY_EFFECTIVE_URL"
    export ANTHROPIC_AUTH_TOKEN="$CC_OPENAI_PROXY_AUTH_TOKEN"
    unset ANTHROPIC_API_KEY

    export ANTHROPIC_MODEL="$selected_model"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="$(cc_openai_qualify_model "''${CC_OPENAI_OPUS_MODEL:-''${CC_OPENAI_MODEL:-''${CC_OPENAI_DEFAULT_MODEL:-gpt-5.6-sol}}}")"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="$sonnet_model"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="$haiku_model"
    export ANTHROPIC_DEFAULT_FABLE_MODEL="$(cc_openai_qualify_model "''${CC_OPENAI_FABLE_MODEL:-''${CC_OPENAI_MODEL:-''${CC_OPENAI_DEFAULT_MODEL:-gpt-5.6-sol}}}")"
    export ANTHROPIC_SMALL_FAST_MODEL="$haiku_model"
    export CLAUDE_CODE_SUBAGENT_MODEL="$selected_model"
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
