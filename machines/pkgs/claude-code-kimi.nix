# cc-kimi: Kimi Code variant of Claude Code. See claude-code-variant.nix for the
# shared launcher. Pairs a Kimi-branded binary (startup label "Kimi Code", Kimi
# thinking verbs + spinner glyphs) with the Kimi coding endpoint, the
# kimi-for-coding model mapping, the Kimi Teal theme, and the Kimi splash.
#
# Auth is out of scope: provide the token via ANTHROPIC_AUTH_TOKEN.
{
  claude-code-variant,
  claude-code-bin,
}:
claude-code-variant {
  command = "cc-kimi";
  binary = claude-code-bin;
  themeFile = ./kimi-teal-theme.json;
  themeSlug = "kimi-teal";
  splash = ./cc-kimi-splash.txt;
  env = {
    ANTHROPIC_BASE_URL = "https://api.kimi.com/coding";
    ANTHROPIC_CUSTOM_HEADERS = "User-Agent: KimiCLI/1.5";
    # Pin the explicit model id rather than the `kimi-for-coding` plan alias,
    # which deliberately remaps under the hood -- being explicit makes model
    # swaps intentional. Bump to kimi-k2.8-code etc. for newer drops (and update
    # the display-name map in patches.py's kimi_brand to match).
    ANTHROPIC_MODEL = "kimi-k2.7-code";
    ANTHROPIC_DEFAULT_HAIKU_MODEL = "kimi-k2.7-code";
    ANTHROPIC_DEFAULT_SONNET_MODEL = "kimi-k2.7-code";
    ANTHROPIC_DEFAULT_OPUS_MODEL = "kimi-k2.7-code";
    ANTHROPIC_SMALL_FAST_MODEL = "kimi-k2.7-code";
    CLAUDE_CODE_SUBAGENT_MODEL = "kimi-k2.7-code";
    API_TIMEOUT_MS = "3000000";
    BASH_DEFAULT_TIMEOUT_MS = "3600000";
    DISABLE_INSTALLATION_CHECKS = "1";
    DISABLE_AUTOUPDATER = "1";
    DISABLE_AUTO_MIGRATE_TO_NATIVE = "1";
    OTEL_RESOURCE_ATTRIBUTES = "model.backend=kimi";
  };
}
