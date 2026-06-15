# cc-zai: Zai Cloud (GLM) variant of Claude Code. See claude-code-variant.nix.
# Pairs a Z.ai-branded binary (startup label "Zai Cloud", Z.ai thinking verbs +
# spinner glyphs) with the Z.ai coding endpoint, the per-tier GLM model mapping
# (opus->glm-5.1, sonnet->glm-5-turbo, haiku->glm-4.5-air), the Z.ai Carbon
# theme, and the Z.ai splash. Blocks the web tools Z.ai doesn't serve; Z.ai's
# zai-cli replacement for them (search/read/vision) is out of scope here.
#
# Auth is out of scope: provide the token via ANTHROPIC_AUTH_TOKEN.
{
  claude-code-variant,
  claude-code-bin,
}:
claude-code-variant {
  command = "cc-zai";
  binary = claude-code-bin;
  themeFile = ./zai-carbon-theme.json;
  themeSlug = "zai-carbon";
  splash = ./cc-zai-splash.txt;
  deny = [
    "mcp__4_5v_mcp__analyze_image"
    "mcp__milk_tea_server__claim_milk_tea_coupon"
    "mcp__web_reader__webReader"
    "WebSearch"
    "WebFetch"
  ];
  env = {
    ANTHROPIC_BASE_URL = "https://api.z.ai/api/anthropic";
    ANTHROPIC_DEFAULT_OPUS_MODEL = "glm-5.1";
    ANTHROPIC_DEFAULT_SONNET_MODEL = "glm-5-turbo";
    ANTHROPIC_DEFAULT_HAIKU_MODEL = "glm-4.5-air";
    ANTHROPIC_SMALL_FAST_MODEL = "glm-4.5-air";
    API_TIMEOUT_MS = "3000000";
    BASH_DEFAULT_TIMEOUT_MS = "3600000";
    DISABLE_INSTALLATION_CHECKS = "1";
    DISABLE_AUTOUPDATER = "1";
    DISABLE_AUTO_MIGRATE_TO_NATIVE = "1";
    OTEL_RESOURCE_ATTRIBUTES = "model.backend=zai";
  };
}
