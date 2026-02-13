{
  lib,
  buildGoModule,
  fetchFromGitHub,
}:

buildGoModule rec {
  pname = "entire";
  version = "0.4.4";

  src = fetchFromGitHub {
    owner = "entireio";
    repo = "cli";
    rev = "v${version}";
    hash = "sha256-6/TsSmJ0z72Ta5ZihO06uV4Mik+fFpm8eCa7d5zlq24=";
  };

  vendorHash = "sha256-rh2VhdwNT5XJYCVjj8tnoY7cacEhc/kcxi0NHYFPYO8=";

  # Relax Go version requirement — project asks for 1.25.6 but nixpkgs
  # has 1.25.4; no language changes between patch versions.
  postPatch = ''
    substituteInPlace go.mod --replace-fail 'go 1.25.6' 'go 1.25.4'
  '';

  subPackages = [ "cmd/entire" ];

  meta = {
    description = "Git workflow tool for capturing AI agent sessions";
    homepage = "https://github.com/entireio/cli";
    license = lib.licenses.mit;
    platforms = lib.platforms.unix;
  };
}
