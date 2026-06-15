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
    # `kimi-for-coding` selects the (subsidized) Kimi Code plan; the `/k2p7`
    # suffix pins K2.7 (released 2026-06-12). Drop the suffix to float with the
    # plan's default, or bump it (k2p8, ...) for newer drops.
    ANTHROPIC_MODEL = "kimi-for-coding/k2p7";
    ANTHROPIC_DEFAULT_HAIKU_MODEL = "kimi-for-coding/k2p7";
    ANTHROPIC_DEFAULT_SONNET_MODEL = "kimi-for-coding/k2p7";
    ANTHROPIC_DEFAULT_OPUS_MODEL = "kimi-for-coding/k2p7";
    ANTHROPIC_SMALL_FAST_MODEL = "kimi-for-coding/k2p7";
    CLAUDE_CODE_SUBAGENT_MODEL = "kimi-for-coding/k2p7";
    API_TIMEOUT_MS = "3000000";
    BASH_DEFAULT_TIMEOUT_MS = "3600000";
    DISABLE_INSTALLATION_CHECKS = "1";
    DISABLE_AUTOUPDATER = "1";
    DISABLE_AUTO_MIGRATE_TO_NATIVE = "1";
    OTEL_RESOURCE_ATTRIBUTES = "model.backend=kimi";
  };
}
