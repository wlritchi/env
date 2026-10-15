# subspace server: the WebSocket server that the `notifications` Claude Code
# plugin's relay connects to (https://github.com/wlritchi/subspace).
#
# `src` is the `subspace` flake input, pinned to a release tag in flake.nix.
# Upstream runs the server as a PEP 723 uv script. Here its few dependencies
# come from nixpkgs instead of the uv lock, so the service never resolves
# packages at start. The script imports ../lib by a path relative to itself,
# so the tree keeps the upstream layout. subspace-protocol is pure Python with
# no install-time steps (its version comes from git tags, which a source
# tarball does not have), so it goes on PYTHONPATH instead of being built.
#
# The remote-agent gateway is not packaged: it needs mcp>=2.3, and it is only
# for agents that cannot run the local relay.
{
  lib,
  stdenvNoCC,
  python3,
  makeWrapper,
  src,
}:

let
  python = python3.withPackages (ps: [
    ps.httpx
    ps.tzdata
    ps.websockets
  ]);
in
stdenvNoCC.mkDerivation {
  pname = "subspace-server";
  version = src.shortRev or "dirty";

  inherit src;

  nativeBuildInputs = [ makeWrapper ];

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    share="$out/share/subspace"
    mkdir -p "$share"
    cp -r lib protocol scripts server "$share/"

    wrap() {
      makeWrapper "${python}/bin/python" "$out/bin/$1" \
        --add-flags "$share/$2" \
        --prefix PYTHONPATH : "$share/protocol/src" \
        --set PYTHONUNBUFFERED 1
    }
    wrap subspace-server server/subspace-server.py
    wrap subspace-migrate-from-notifications scripts/migrate-from-notifications.py
    wrap subspace-new-credential scripts/new-credential.py

    runHook postInstall
  '';

  meta = {
    description = "Persistent WebSocket server for Claude Code notifications and agent messaging";
    homepage = "https://github.com/wlritchi/subspace";
    mainProgram = "subspace-server";
    platforms = lib.platforms.unix;
  };
}
