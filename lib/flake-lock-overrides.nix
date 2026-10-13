# Print one `INPUT_PATH FLAKEREF` line for each locked input of the flake.lock
# JSON in $WLR_FLAKE_LOCK, at every depth (for example `nix-homebrew/brew-src`).
# Inputs that only follow another input have no lock entry of their own and are
# not printed. Evaluate with `nix eval --raw --impure --file`.
let
  lock = builtins.fromJSON (builtins.getEnv "WLR_FLAKE_LOCK");

  inputLines =
    prefix: nodeName:
    let
      inputs = lock.nodes.${nodeName}.inputs or { };
    in
    builtins.concatMap (
      name:
      let
        target = inputs.${name};
        path = prefix + name;
      in
      # A list is a `follows` path, not a node name.
      if builtins.isList target then
        [ ]
      else
        [ "${path} ${builtins.flakeRefToString lock.nodes.${target}.locked}" ]
        ++ inputLines "${path}/" target
    ) (builtins.attrNames inputs);
in
builtins.concatStringsSep "" (map (line: line + "\n") (inputLines "" lock.root))
