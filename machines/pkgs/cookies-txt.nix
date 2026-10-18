# cookies.txt WebExtension, built from the fork with the partitioned-cookies
# fix (hrdl-github/cookies-txt has not merged it). The output is the XPI file.
#
# The upstream manifest has no gecko ID, and Firefox does not install an
# extension from policy without one. Use the AMO ID so that this build
# replaces an AMO install and keeps its data.

{
  stdenvNoCC,
  fetchFromGitHub,
  jq,
  zip,
}:

let
  extensionId = "{12cf650b-1822-40aa-bff0-996df6948878}";
in
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "cookies-txt";
  version = "1.0-unstable-2026-01-30";
  # Keep the .xpi suffix on the store path used as the policy install_url
  name = "${finalAttrs.pname}-${finalAttrs.version}.xpi";

  src = fetchFromGitHub {
    owner = "wlritchi";
    repo = "cookies-txt";
    rev = "7f66b8cf54bbb2881a1b7b5c99ea33541ebf300a"; # include-partitioned-cookies
    hash = "sha256-DLPlPOwE/BWvsh5q4Uef5NAib20NOHFKc82vnNEM3ts=";
  };

  nativeBuildInputs = [
    jq
    zip
  ];

  passthru = { inherit extensionId; };

  buildPhase = ''
    jq --arg id '${extensionId}' '.browser_specific_settings.gecko.id = $id' \
      manifest.json > manifest.json.new
    mv manifest.json.new manifest.json
    zip -rX cookies-txt.xpi manifest.json background.js content.js popup icons _locales
  '';

  installPhase = ''
    cp cookies-txt.xpi $out
  '';
})
