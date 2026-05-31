# ccstatusline: customizable status line formatter for Claude Code.
#
# Published to npm as a self-contained bundle (zero runtime `dependencies`), so
# there is no lockfile/node_modules to resolve: we just fetch the registry
# tarball and wrap dist/ccstatusline.js with node. No buildNpmPackage and no
# npmDepsHash to maintain.
#
# Version bump: update `version` and `hash`. The hash is npm's own SRI integrity
# string, so no fake-hash dance is needed:
#   curl -s https://registry.npmjs.org/ccstatusline/<version> | jq -r .dist.integrity
{
  lib,
  stdenvNoCC,
  fetchurl,
  nodejs,
  makeWrapper,
}:

stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "ccstatusline";
  version = "2.2.19";

  src = fetchurl {
    url = "https://registry.npmjs.org/ccstatusline/-/ccstatusline-${finalAttrs.version}.tgz";
    hash = "sha512-Z0AHBr1kMLYTJE5wYHp7GR4mOer6TGfa+ze0jj96vOVs9zwx1DMG4zqxFYY84lT03w4WM+notHs6JanrOZ0LFw==";
  };

  nativeBuildInputs = [ makeWrapper ];

  # The npm tarball unpacks into ./package, which stdenv enters automatically;
  # there is nothing to configure or compile.
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/lib/ccstatusline"
    cp -r . "$out/lib/ccstatusline"

    makeWrapper "${nodejs}/bin/node" "$out/bin/ccstatusline" \
      --add-flags "$out/lib/ccstatusline/dist/ccstatusline.js"

    runHook postInstall
  '';

  meta = {
    description = "Customizable status line formatter for Claude Code CLI";
    homepage = "https://github.com/sirmalloc/ccstatusline";
    license = lib.licenses.mit;
    mainProgram = "ccstatusline";
    platforms = lib.platforms.all;
  };
})
