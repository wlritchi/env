{ pkgs, ... }:

{
  home.packages = with pkgs; [ browserpass ];

  custom.librewolf.enable = true;
  # Profile created by LibreWolf before home-manager managed it
  programs.librewolf.profiles.default.path = "80pmiiap.default-default";
}
