# claude-code: Anthropic's Claude Code CLI, patched at build time by wlrenv.ccpatch.
#
# Fetches the platform-specific Bun standalone binary from npm (the real binary
# lives in @anthropic-ai/claude-code-<platform>, not the meta package), runs the
# pure-Python ccpatch pipeline over it (length-free JS patches + Bun repack), and
# wraps it in a launcher that configures terminal-specific runtime behavior.
#
# The patch step needs no network and -- on Linux -- no dependencies beyond
# python3 (stdlib). On Darwin the Mach-O repack path needs python3Packages.lief.
#
# Version bump: update `version` and the four `hash`es. Each hash is npm's own
# SRI integrity string:
#   curl -s https://registry.npmjs.org/@anthropic-ai/claude-code-<plat>/<ver> \
#     | jq -r .dist.integrity
#
# NOTE: the binary targets FHS loader paths (/lib64/ld-linux-x86-64.so.2); it is
# intentionally NOT patchelf'd (that risks disturbing the embedded .bun blob) and
# relies on the host loader. Fine on Arch; on NixOS proper it would need an FHS
# wrapper. The build skips the runtime smoke test (--no-smoke) because the
# sandbox lacks that loader; the structural verify (re-extract + marker check)
# still runs.
{
  lib,
  stdenvNoCC,
  fetchurl,
  python3,
  cc-openai-proxy-launcher,
}:

let
  version = "2.1.179";

  sources = {
    "x86_64-linux" = {
      platform = "linux-x64";
      hash = "sha512-jyVJpNIPIpqNr24bEGGgkWDY8YSi/h1+wy9Nzfsdwk6I/OTa0/iV7C3KDJTCkALXRRxDCgJXK2rjoWzXDV7Vpw==";
    };
    "aarch64-linux" = {
      platform = "linux-arm64";
      hash = "sha512-KpB1gvSGW0m73be0gKbIqbKfyu0zTjf7oXuX6U1QfYKZVxarhA/D9n91c/sR6e2tB75nohx24Kg5roL6JvCYdQ==";
    };
    "aarch64-darwin" = {
      platform = "darwin-arm64";
      hash = "sha512-OgeqVYNy1oUOigY94O1TNsi1HTOMxocZ9rphNH3oc6sCjFjRl8gV+3H1TCTdH5fXobAUAsyx36WoBoXO0MhBWA==";
    };
    "x86_64-darwin" = {
      platform = "darwin-x64";
      hash = "sha512-okADatergdFzzTbGtWqtiRwlHJqvIqWUOQXIG8PqljADjIGTcGa2I8rZ+9HK0E1F6ZkF8uuhHVGY5EN0GKVWgw==";
    };
  };

  system = stdenvNoCC.hostPlatform.system;
  source = sources.${system} or (throw "claude-code: unsupported system ${system}");

  # ccpatch is pure-stdlib on Linux; the Mach-O path needs lief.
  pythonEnv =
    if stdenvNoCC.hostPlatform.isDarwin then python3.withPackages (ps: [ ps.lief ]) else python3;

  # Only the bits of the wlrenv source tree ccpatch needs, so unrelated edits
  # don't invalidate this build.
  ccpatchSrc = lib.fileset.toSource {
    root = ../../src;
    fileset = lib.fileset.unions [
      ../../src/wlrenv/__init__.py
      ../../src/wlrenv/ccpatch
    ];
  };
in
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "claude-code-patched";
  inherit version;

  src = fetchurl {
    url = "https://registry.npmjs.org/@anthropic-ai/claude-code-${source.platform}/-/claude-code-${source.platform}-${version}.tgz";
    inherit (source) hash;
  };

  # npm tarball unpacks into ./package (entered automatically by stdenv).
  dontConfigure = true;

  buildPhase = ''
    runHook preBuild

    export PYTHONDONTWRITEBYTECODE=1
    PYTHONPATH=${ccpatchSrc} ${pythonEnv}/bin/python -m wlrenv.ccpatch.cli apply \
      ./claude -o ./claude-patched --version ${version} --no-smoke

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    install -Dm755 ./claude-patched "$out/libexec/claude-code/claude"

    # The launcher configures terminal-specific behavior, then runs the patched
    # binary. Keep it as a script (not makeWrapper --add-flags) for the
    # truecolor tweak, which is conditional on the terminal. (Dev-channel
    # inheritance used to live here as an env hack; it is now done natively in
    # the binary by the dev-channel-inheritance patch -- nothing to do here.)
    mkdir -p "$out/bin"
    cat > "$out/bin/claude" <<EOF
    #!/usr/bin/env bash
    source ${cc-openai-proxy-launcher}
    if ! cc_openai_proxy_configure optional claude && [ "\''${CC_OPENAI_PROXY_URL+x}" = x ]; then
      exit 1
    fi

    # Lift Claude's tmux 256-color cap when the terminal advertises truecolor.
    if [ -z "\''${CLAUDE_CODE_TMUX_TRUECOLOR:-}" ] && { [ "\''${COLORTERM:-}" = truecolor ] || [ "\''${COLORTERM:-}" = 24bit ]; }; then
      export CLAUDE_CODE_TMUX_TRUECOLOR=1
    fi
    # (--thinking-display summarized was prepended here to make 4.7+ models
    # return thinking content. No longer needed: the showThinkingSummaries
    # setting drives the request's thinking.display, which the binary now
    # plumbs through to the server -- the old "property never sent" bug is gone.)
    exec "$out/libexec/claude-code/claude" "\$@"
    EOF
    chmod +x "$out/bin/claude"

    runHook postInstall
  '';

  # The binary is already a self-contained Bun executable; don't let stdenv's
  # fixup strip or patchelf it (would disturb the embedded .bun blob).
  dontStrip = true;
  dontPatchELF = true;
  dontFixup = true;

  meta = {
    description = "Claude Code CLI, build-time patched via wlrenv.ccpatch";
    homepage = "https://github.com/anthropics/claude-code";
    license = lib.licenses.unfree;
    mainProgram = "claude";
    platforms = builtins.attrNames sources;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
})
