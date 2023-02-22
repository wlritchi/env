* add os-specific configs
  * want to use this for alacritty, which should have `save_to_clipboard: true` on windows and macos
  * idea: post-checkout build step based on a "default" and on os-specific patch files?
    * would need to write the post-checkout hook
      * remember that macos is stupid about coreutils so check for gpatch first
    * would need a pre-commit hook to validate that every patch applies
