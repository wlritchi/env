# cc-minimax: MiniMax Cloud variant of Claude Code. See claude-code-variant.nix.
# Pairs a MiniMax-branded binary (startup label "MiniMax Cloud", MiniMax thinking
# verbs + spinner glyphs) with the MiniMax endpoint (everything routes to
# MiniMax-M2.7), the MiniMax Nebula theme, and the MiniMax splash. Blocks
# WebSearch (MiniMax serves web search via its own MCP server, which -- like
# Z.ai's zai-cli -- is out of scope here).
#
# Auth is out of scope: provide the token via ANTHROPIC_AUTH_TOKEN.
{
  claude-code-variant,
  claude-code-bin,
}:
claude-code-variant {
  command = "cc-minimax";
  binary = claude-code-bin;
  themeFile = ./minimax-nebula-theme.json;
  themeSlug = "minimax-nebula";
  splash = ./cc-minimax-splash.txt;
  deny = [ "WebSearch" ];
  env = {
    ANTHROPIC_BASE_URL = "https://api.minimax.io/anthropic";
    ANTHROPIC_MODEL = "MiniMax-M2.7";
    ANTHROPIC_DEFAULT_OPUS_MODEL = "MiniMax-M2.7";
    ANTHROPIC_DEFAULT_SONNET_MODEL = "MiniMax-M2.7";
    ANTHROPIC_DEFAULT_HAIKU_MODEL = "MiniMax-M2.7";
    ANTHROPIC_SMALL_FAST_MODEL = "MiniMax-M2.7";
    # MiniMax recommends disabling Claude Code's non-essential background traffic.
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1";
    API_TIMEOUT_MS = "3000000";
    BASH_DEFAULT_TIMEOUT_MS = "3600000";
    DISABLE_INSTALLATION_CHECKS = "1";
    DISABLE_AUTOUPDATER = "1";
    DISABLE_AUTO_MIGRATE_TO_NATIVE = "1";
    OTEL_RESOURCE_ATTRIBUTES = "model.backend=minimax";
  };
}
