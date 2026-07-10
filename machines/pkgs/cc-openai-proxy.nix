{
  lib,
  buildNpmPackage,
  makeWrapper,
  nodejs_24,
}:

buildNpmPackage {
  pname = "cc-openai-proxy";
  version = "0.1.0";

  src = ../../src/cc-openai-proxy;
  npmDepsHash = "sha256-kDb7c+rnm+szN48c8a+cK5KPGyl0sSQ+CgnclXoT8sY=";
  nodejs = nodejs_24;

  dontNpmBuild = true;
  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/libexec/cc-openai-proxy" "$out/bin"
    cp -R bin package.json node_modules "$out/libexec/cc-openai-proxy/"
    makeWrapper ${nodejs_24}/bin/node "$out/bin/cc-openai-proxy" \
      --add-flags "$out/libexec/cc-openai-proxy/bin/cc-openai-proxy.js"

    runHook postInstall
  '';

  meta = {
    description = "Anthropic Messages proxy from Claude Code to pi-ai OpenAI Codex";
    license = lib.licenses.mit;
    mainProgram = "cc-openai-proxy";
    platforms = lib.platforms.unix;
  };
}
