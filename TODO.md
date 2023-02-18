* add os-specific configs
  * want to use this for alacritty, which should have `save_to_clipboard: true` on windows and macos
  * idea: post-checkout build step based on a "default" and on os-specific patch files?
    * would need to write the post-checkout hook
      * remember that macos is stupid about coreutils so check for gpatch first
    * would need a pre-commit hook to validate that every patch applies
* switch `gsha` to use `--staged` (new in git 2.35)
* add `gtd` for `git tag --delete`, and `gtdr` for however you delete a remote tag
* add `gbdr` for however you delete a remote branch
* use aliases to resolve `grm` git helper since macos is stupid about coreutils and installing the gnu versions causes `grm` to be a name for `rm`
