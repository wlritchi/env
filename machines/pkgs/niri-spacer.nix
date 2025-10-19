{ lib, rustPlatform, pkg-config, libxkbcommon, wayland, wayland-protocols
, makeWrapper, sccache }:

rustPlatform.buildRustPackage rec {
  pname = "niri-spacer";
  version = "0.1.0";

  src = ../../rust-utils/niri-spacer;

  cargoLock = { lockFile = ../../rust-utils/niri-spacer/Cargo.lock; };

  nativeBuildInputs = [ pkg-config makeWrapper ];

  buildInputs = [ libxkbcommon wayland wayland-protocols ];

  # Enable sccache for faster incremental builds
  RUSTC_WRAPPER = "${sccache}/bin/sccache";

  # Wayland applications need to find libwayland-client.so at runtime
  postInstall = ''
    wrapProgram $out/bin/niri-spacer \
      --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath [ wayland ]}"
  '';

  meta = with lib; {
    description =
      "A persistent utility to spawn and manage placeholder windows in niri workspaces";
    homepage = "https://github.com/YaLTeR/niri-spacer";
    license = with licenses; [ mit asl20 ];
    maintainers = [ ];
    platforms = platforms.linux;
  };
}
