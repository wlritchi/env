# claude-code: Anthropic's Claude Code CLI, patched at build time by wlrenv.ccpatch.
#
# Fetches the platform-specific Bun standalone binary from npm (the real binary
# lives in @anthropic-ai/claude-code-<platform>, not the meta package), runs the
# pure-Python ccpatch pipeline over it (length-free JS patches + Bun repack), and
# wraps it in a launcher that reproduces the runtime env the claude shim sets.
#
# The patch step needs no network and -- on Linux -- no dependencies beyond
# python3 (stdlib). On Darwin the Mach-O repack path needs python3Packages.lief.
#
# Version bump: update `version` and the three `hash`es. Each hash is npm's own
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
  makeWrapper,
}:

let
  version = "2.1.170";

  sources = {
    "x86_64-linux" = {
      platform = "linux-x64";
      hash = "sha512-SSQ6TsGbZJSC1s6R5pxlTZPq1bilSpoTR8JANOq8ALUkbRVhgVSl0PiSSNSnc3zNdDCA1iA3ywLmAuISuhlvKA==";
    };
    "aarch64-darwin" = {
      platform = "darwin-arm64";
      hash = "sha512-lnBfVVTO+Wk31IAh5KDOY+Cuu1vIHC3N3UjHY9SEroDat8XKqjFtckY50jPi50m5x0oWkeQiyDl4nPstgdkNwQ==";
    };
    "x86_64-darwin" = {
      platform = "darwin-x64";
      hash = "sha512-w2lZwSsKDVqrY8O6N65SSP309JJleWrUx9tltW2SIGaPRLybtrZf7q6KxDz3I/gEMBhpwnC2MHXYMU0sw6JXzg==";
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

  nativeBuildInputs = [ makeWrapper ];

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

    # Launcher: reproduces the runtime env the claude shim sets, then execs the
    # patched binary. Kept as a script (not makeWrapper --add-flags) because two
    # of the tweaks are conditional on argv / the terminal.
    mkdir -p "$out/bin"
    cat > "$out/bin/claude" <<EOF
    #!/usr/bin/env bash
    # Lift Claude's tmux 256-color cap when the terminal advertises truecolor.
    if [ -z "\''${CLAUDE_CODE_TMUX_TRUECOLOR:-}" ] && { [ "\''${COLORTERM:-}" = truecolor ] || [ "\''${COLORTERM:-}" = 24bit ]; }; then
      export CLAUDE_CODE_TMUX_TRUECOLOR=1
    fi
    # Let bg agents inherit --dangerously-load-development-channels via the env
    # (the dev-channel-inheritance patch reads CLAUDE_DEV_CHANNELS on the worker).
    if [ -z "\''${CLAUDE_DEV_CHANNELS:-}" ]; then
      specs=""; collecting=0
      for a in "\$@"; do
        case "\$a" in
          --dangerously-load-development-channels) collecting=1 ;;
          --dangerously-load-development-channels=*) specs="\$specs \''${a#*=}" ;;
          -*) collecting=0 ;;
          *) [ "\$collecting" = 1 ] && specs="\$specs \$a" ;;
        esac
      done
      [ -n "\$specs" ] && export CLAUDE_DEV_CHANNELS="\''${specs# }"
    fi
    # Prepend --thinking-display summarized so 4.7+ models return content; a
    # user-supplied value later in argv still wins.
    exec "$out/libexec/claude-code/claude" --thinking-display summarized "\$@"
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
