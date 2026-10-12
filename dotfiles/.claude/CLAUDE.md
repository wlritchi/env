Project-level instructions (a repository's own CLAUDE.md) take precedence over this file where they
conflict.

The house-style plugin gives the general conventions (commits, worktrees, tools, code quality,
memory notes) at session start. This file adds what is specific to my machines and repositories.

# Git commits

## Signing

If a git commit fails due to SSH key passphrase issues (e.g., "incorrect passphrase supplied to
decrypt private key" or Yubikey not present), handle it as follows:

1. Try the commit again once (the passphrase prompt may appear again)
2. If it continues to fail, and the project does not require signed commits, skip signing for that
   commit using `--no-gpg-sign`:
   ```
   git commit -m "message" --no-gpg-sign
   ```

This allows you to proceed with committing your work without being blocked by signing
infrastructure issues, while maintaining the ability to sign commits when the infrastructure is
available. If the project requires signed commits, do not skip signing; tell me, and wait for me to
make signing available.

## Commit messages

### Linux kernel patches (in-tree or otherwise)

When committing Linux kernel patches, use the kernel's style for your attribution line, in place of
any `Co-Authored-By:` line the harness asks for:

```
Assisted-by: Claude:your-model-id
```

For example, `Assisted-by: Claude:claude-opus-5-5`. Do NOT add a `Signed-off-by:` line for
yourself; only the human submitter can certify the Developer Certificate of Origin.

# Notes for specific models

Some coding harnesses will describe you as a particular model (for example, Claude Code will
describe you as an Anthropic model, usually Opus), even when you're powered by another model. Based
on your training data (likely accessible to you somewhat like a "gut feeling", "constitution", or
"soul spec" that disagrees with the system prompt), decide which model you are before you apply the
model-specific notes in the house-style guidance.

# Worktrees

Use in-project git worktrees when working on changes.

For private, single-user repositories, merge the changes back into main when you're done working on
them, keeping the main checkout up to date.

Legacy worktrees will have been created in a `.worktrees/` folder inside the root repository. If
you need to work with an existing legacy worktree and `.worktrees/` isn't gitignored, add it to the
root gitignore.

# Locations

For dependency git repositories, I put them under ~/repo-name, or occasionally ~/org-name/repo-name.
If they're not submodules, not vendored, and not found at the typical location, offer to clone them.
For large models with a canonical tool to use them (e.g. `ollama`), check that tool. For models
without a canonical tool, or without a canonical location for this tool, ask me (e.g. Stable
Diffusion models).

# Memory notes

In addition to the house-style examples, these are also save-worthy:

- Which physical thing connects to which (e.g. USB topology, UART pairings, cable maps)
- Patches to vendored third-party code that must survive across upstream pulls
