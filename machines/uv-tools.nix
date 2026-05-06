{
  config,
  lib,
  pkgs,
  ...
}:

let
  currentPlatform = if pkgs.stdenv.isDarwin then "darwin" else "linux";

  toolType = lib.types.submodule {
    options = {
      spec = lib.mkOption {
        type = lib.types.str;
        description = "Argument passed to `uv tool install` (PyPI spec, git+URL, or path).";
      };
      python = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Python version pin, passed as `--python` if non-null.";
      };
      withDeps = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        description = "Extra deps for the tool's environment, each passed as `--with`.";
      };
      platforms = lib.mkOption {
        type = lib.types.listOf (
          lib.types.enum [
            "linux"
            "darwin"
          ]
        );
        default = [
          "linux"
          "darwin"
        ];
        description = "Platforms on which to install this tool.";
      };
      disabled = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "If true, exclude this tool from the manifest. Overlay-friendly suppression.";
      };
    };
  };

  resolved = lib.filterAttrs (
    _name: tool: !tool.disabled && lib.elem currentPlatform tool.platforms
  ) config.home.uvTools;

  manifest = pkgs.writeText "wlrenv-uv-tools.json" (builtins.toJSON resolved);
in
{
  options.home.uvTools = lib.mkOption {
    type = lib.types.attrsOf toolType;
    default = { };
    description = "Python CLIs to install via `uv tool install`. Keyed by tool name.";
  };

  config = {
    home.uvTools = { };

    home.file.".config/wlrenv/uv-tools.json".source = manifest;
  };
}
