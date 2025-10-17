{ lib, rustPlatform, pkg-config, libxkbcommon, wayland, wayland-protocols }:

rustPlatform.buildRustPackage rec {
  pname = "niri-spacer";
  version = "0.1.0";

  src = ../../rust-utils/niri-spacer;

  cargoLock = { lockFile = ../../rust-utils/niri-spacer/Cargo.lock; };

  nativeBuildInputs = [ pkg-config ];

  buildInputs = [ libxkbcommon wayland wayland-protocols ];

  meta = with lib; {
    description =
      "A persistent utility to spawn and manage placeholder windows in niri workspaces";
    homepage = "https://github.com/YaLTeR/niri-spacer";
    license = with licenses; [ mit asl20 ];
    maintainers = [ ];
    platforms = platforms.linux;
  };
}
