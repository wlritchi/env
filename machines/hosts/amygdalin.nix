{ pkgs, ... }:

{
  home.packages = with pkgs; [ browserpass ];
}
