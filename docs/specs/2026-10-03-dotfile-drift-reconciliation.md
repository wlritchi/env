# Dotfile Drift Reconciliation

## Problem

Dotfiles are symlinked from `$HOME` into the repos (unpatched) or into
`rendered/` (patched). Some programs rewrite their own config files. The edits
land in the rendered file or in the repo working tree, or the program replaces
the symlink with a regular file. A later re-render silently discards them, and
nothing offers to keep them.

## Reference files

Every render writes `rendered/$path.reference`, a pristine copy of the output.
Drift for a patched file is `diff(reference, live)`. For an unpatched file the
live file is the working tree copy, so drift is `diff(HEAD, working tree)`.

`wlr-sync-dotfiles` refuses to re-render over a drifted file without asking.

## wlr-reconcile-dotfiles

Runs from `wlr-check-update` before the pull (interactive sessions only), and
by hand. Steps per dotfile:

1. Resolve base file and patch layers via `wlr-sync-dotfiles --resolve`, so the
   layering rules have one implementation. The tool derives the candidate
   destinations itself and cross-checks them against the sync script's layers.
2. Read the live content through `$HOME/$path`, whether symlink or detached
   regular file. Compare with the expected content.
3. If they differ, run the repo's pre-commit hooks (`prek run --files`) on the
   live content, by writing it temporarily to the tracked base file and
   restoring it afterwards. Formatting-only differences disappear here.
4. Walk hunks like `git add -p`: accept, leave, revert, split, or quit. Accepted
   hunks go to one destination per pass:

   | Destination        | Base belongs to that repo | Base belongs to the other repo        |
   |--------------------|---------------------------|---------------------------------------|
   | global             | edit the base file        | `patches/global/$path.patch`          |
   | uname              | `patches/uname/$(uname)/$path.patch`                              |
   | host               | `patches/host/$HOSTNAME/$path.patch`                              |

   Public destinations are hidden when a private base shadows the public file.
   Folding into layer k rewrites that patch as `diff(stage k-1, stage k + hunks)`
   and verifies that later layers still apply. A patch that becomes empty is
   removed.
5. Re-render from the updated repos. The reference becomes the new render; the
   live file becomes the render plus any hunks left in place. Detached files are
   re-linked.
6. Stage touched files per repo and offer one commit per repo.

## Update flow

`wlr-check-update` reconciles first, then fetches and verifies the remote
commit. If the local branch is ahead, nothing is pulled. If it has diverged
(reconcile commits on an older upstream), it rebases onto the verified remote
commit instead of failing the fast-forward; a dirty tree or failed rebase
aborts the update with instructions.

## Non-goals

- Binary dotfiles: reported and skipped.
- Removing an existing layer from the tool (only possible by cancelling it).
- Editing hunk text inline.
