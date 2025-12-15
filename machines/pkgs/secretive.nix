# Secretive - SSH key management via macOS Secure Enclave
#
# IMPORTANT: When updating the version, verify the SLSA attestation before updating the hash:
#   curl -L -o /tmp/Secretive.zip https://github.com/maxgoedjen/secretive/releases/download/vX.Y.Z/Secretive.zip
#   gh attestation verify /tmp/Secretive.zip --repo maxgoedjen/secretive
#   nix-hash --type sha256 --base32 /tmp/Secretive.zip
#
# Only update the hash if attestation verification succeeds (exit code 0).

{
  lib,
  stdenv,
  fetchurl,
  unzip,
}:

stdenv.mkDerivation rec {
  pname = "secretive";
  version = "3.0.4";

  src = fetchurl {
    url = "https://github.com/maxgoedjen/secretive/releases/download/v${version}/Secretive.zip";
    sha256 = "1fl4s2hnjzdhq9gm6vh8j4d17lxsdl9s00596i90fca45s0hfvb9";
  };

  # The zip contains a .app bundle directly
  sourceRoot = ".";

  nativeBuildInputs = [ unzip ];

  # Skip standard build phases - this is a pre-built binary
  dontBuild = true;
  dontFixup = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/Applications
    cp -r Secretive.app $out/Applications/
    runHook postInstall
  '';

  meta = with lib; {
    description = "Store SSH keys in the Secure Enclave";
    homepage = "https://github.com/maxgoedjen/secretive";
    license = licenses.mit;
    platforms = platforms.darwin;
    maintainers = [ ];
    sourceProvenance = [ sourceTypes.binaryNativeCode ];
  };
}
