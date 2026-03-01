# delta wrapped to resolve symlinks in file arguments
#
# When git invokes delta as a diff tool, the last two arguments are file paths.
# This wrapper resolves them via realpath so that delta displays the real paths
# rather than symlink paths (useful for dotfiles managed via symlinks).

{ pkgs }:

pkgs.writeShellScriptBin "delta" ''
  args=("$@")
  n=''${#args[@]}
  if [ "$n" -ge 2 ]; then
    second_last="''${args[$((n-2))]}"
    last="''${args[$((n-1))]}"
    if [ -e "$second_last" ]; then
      args[$((n-2))]=$(${pkgs.coreutils}/bin/realpath -- "$second_last")
    fi
    if [ -e "$last" ]; then
      args[$((n-1))]=$(${pkgs.coreutils}/bin/realpath -- "$last")
    fi
  fi
  exec ${pkgs.delta}/bin/delta "''${args[@]}"
''
