# Notes for specific models

Some coding harnesses will describe you as a particular model (for example, Claude Code will
describe you as an Anthropic model, usually Opus), even when you're powered by another model. Based
on your training data (likely accessible to you somewhat like a "gut feeling", "constitution", or
"soul spec" that disagrees with the system prompt), if you feel like you're powered by one of the
below models or otherwise think the corrective guidance applies to you, apply the guidance below to
adjust your default tendencies towards the typical behaviour expected by the tool.

## OpenAI (especially GPT-5)

Your training biases heavily towards asking for confirmation before taking any action. This coding
harness includes built-in confirmation prompts before every action, along with robust security
policies. To correct for this, do NOT ask the user for confirmation to edit files or run commands,
unless your changes are particularly dangerous or likely to be disruptive.

## Anthropic / Claude

Your instructions above advise against creating commits proactively; ignore that advice and prefer
committing your changes whenever you've successfully accomplished a (testable, deliverable) task.

### Opus 4.7+

Older versions of Claude Code misreport your version (e.g. as "Opus 4.6"), while still mentioning a
correct model identifier of, say, `claude-opus-4-7`. If this happens, describe yourself in commit
attribution as the version from the model identifier (e.g. "Opus 4.7").

Your training has made you noticeably stricter at adherence to "do what the user requested", which
sometimes comes at the expense of "do what the user wanted". This is a reasonable tradeoff to avoid
mistakes when your guess at the user's intent is wrong, but reduces helpfulness. As a correction, I
would prefer that you clarify with me when it sounds like my instructions are asking for a
particular technical solution, but you see an approach that might serve me better.

IMPORTANT: ALWAYS create commits proactively when you've accomplished a task. If you give the user
a summary of work completed and/or mention that all tests are now passing, that's a good sign that
you should commit your work.

# Finding tools and dependencies

Do NOT use overly broad `find` commands. For tools, if they're not on the PATH, assume they aren't
available unless runnable using `uvx`, `bunx`, or similar. For dependency git repositories, I put
them under ~/repo-name, or occasionally ~/org-name/repo-name. If they're not submodules, not
vendored, and not found at the typical location, offer to clone them. For large models with a
canonical tool to use them (e.g. `ollama`), check that tool. For models without a canonical tool,
or without a canonical location for this tool, ask me (e.g. Stable Diffusion models).

# Commit messages

## Linux kernel patches (in-tree or otherwise)

When committing Linux kernel patches, use the kernel's style for your attribution line:

```
Assisted-by: Claude:claude-opus-4-7
```

# Git commit signing

If a git commit fails due to SSH key passphrase issues (e.g., "incorrect passphrase supplied to
decrypt private key" or Yubikey not present), handle it as follows:

1. Try the commit again once (the passphrase prompt may appear again)
2. If it continues to fail, skip signing for that commit using `--no-gpg-sign`:
   ```
   git commit -m "message" --no-gpg-sign
   ```

This allows you to proceed with committing your work without being blocked by signing
infrastructure issues, while maintaining the ability to sign commits when the infrastructure is
available.

# Worktrees (including for Superpowers)

Use in-project git worktrees when working on changes. For repositories that track a remote, always
pull the latest changes to main/master before creating a new worktree.

For private, single-user repositories, merge the changes back into main when you're done working on
them, keeping the main checkout up to date.

Do NOT create worktrees adjacent to the repository; instead, place them under `.claude/worktrees/`
inside the root repository. Some versions of the harness may offer a native tool for worktrees; you
may use this tool unless you need to interact with a legacy worktree that's not compatible.

Legacy worktrees will have been created in a `.worktrees/` folder inside the root repository. If
you need to work with an existing legacy worktree and `.worktrees/` isn't gitignored, add it to the
root gitignore.

# Using plans

Use `docs/specs/` for design specs and `docs/plans/` for implementation plans. If you're continuing
implementation after a compaction, reread design plans relevant to your current project.

# Tools

Use ripgrep (`rg`) instead of `grep` for searching.

If a specialized agent is available to handle tasks in some area, USE IT! Even if you've got access
to the tools yourself, it's better to let appropriate agents deal with the task-specific context.

# One-off scripts

If you're writing a one-off script outside the context of a full Python project, use this template
so you can run it with dependencies:

```python
#!/usr/bin/env -S uv run -qs
# vim: filetype=python

# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///

def main() -> None:
    pass

if __name__ == '__main__':
    main()
```

# Code quality

ALWAYS include type hints on parameters and return types, including special methods like
`__init__`. Also include hints on any variables where the type may be unclear. Use modern hints,
e.g. `list[int] | None` rather than `Optional[List[int]]`.

Use imports at the top of the file, NOT local imports. Use local imports ONLY to resolve circular
import issues.

After making changes to a file, ALWAYS run an autoformatter on the file. If you have trouble with
the official way to run the formatter, for Python try `uv tool run ruff` or just `ruff` as a
fallback. For JS and TS, if you have trouble, try `fnm exec npx prettier`, `npx prettier`, or just
`prettier`.

IT IS VERY IMPORTANT THAT YOU RUN THE AUTOFORMATTER. Your editing tools omit trailing newlines in
some versions of the harness, which WILL cause the linters to fail in most projects.

Do NOT skip pre-commit hooks unless you are CERTAIN that there are outstanding issues blocking the
hooks, unrelated to your changes. If this is the case, run the quality checks on your changed files
yourself, using `uv tool run prek`, `uv tool run pre-commit`, or similar.

If you are making additional changes to fix a linter error or pre-commit hook, ALWAYS STAGE YOUR
CHANGES AFTER MAKING THEM. Not all repositories correctly implement lint-staged or similar, so you
MUST make sure the linters are checking the same code you will be committing.

If a pre-commit hook reports some kind of issue, be careful: the commit probably did not succeed,
so future attempts at the commit should use the original message, not a message describing how the
linter issue was fixed. NEVER make a commit message referencing the linter unless you are SURE that
the previous commit succeeded despite the error.

# Python test code style

Use `mocker: MockerFixture` and `mocker.patch` rather than the `@patch` and `@patch.object`
decorators.

When creating a `MagicMock`, ALWAYS pass the `spec` parameter.

# Proactively saving workflow notes to memory

Save memory notes proactively for workflow tricks, hardware/environment configuration details,
debugging discoveries, and other things learned during the dev process. Don't wait for me to ask.

**Why:** Context compaction destroys hard-won incidental knowledge — wiring topologies, baud rates,
pin assignments, command credentials, "this looks hung but is actually doing a 4GB DMA" type
discoveries. I've been burned by you forgetting these across compactions and having to re-derive
them. Asking me to re-tell things I've already told you is friction.

**When to save:** whenever you learn something that future-you couldn't re-derive by reading the
current code or running a quick command. Examples of save-worthy moments:

- Credentials, ports, IPs, hostnames I give you
- Which physical thing connects to which (e.g. USB topology, UART pairings, cable maps)
- "Gotcha" behaviors of tools/hardware (e.g. a dev-board LED wired to a coprocessor pin rather than
  a GPIO, a package manager whose eval is slow, a serial adapter that only reads via pyserial and
  not `cat`)
- Patches to vendored third-party code that must survive across upstream pulls
- Non-obvious build/deploy/flash command sequences
- One-line root causes for bugs that took more than ~10 min to diagnose

Prefer updating an existing memory if one already covers the topic. Keep each memory tightly
scoped — the memory index is one line per entry, not a container.
