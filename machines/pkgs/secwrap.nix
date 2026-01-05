{
  pkgs,
  backend ? "pass",
}:

pkgs.writeShellScriptBin "secwrap" ''
  set -euo pipefail

  # secwrap - wrap commands with secrets from pass/passage
  #
  # Usage: secwrap <command> [args...]
  #
  # Looks up "config/env/<command>" in the configured password store.
  # If found, parses KEY=VALUE lines and exports them before exec'ing the command.
  # If not found, exec's the command directly (no-op for tools without secrets).

  if [[ $# -lt 1 ]]; then
      echo "Usage: secwrap <command> [args...]" >&2
      exit 1
  fi

  tool_name="$1"
  shift

  secret_path="config/env/''${tool_name}"

  # Try to fetch secrets from the password store
  secrets=""
  if secrets=$(${backend} show "$secret_path" 2>/dev/null); then
      # Parse KEY=VALUE lines and export them
      while IFS= read -r line; do
          # Skip empty lines and comments
          [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
          # Only process lines that look like KEY=VALUE
          if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
              export "$line"
          fi
      done <<< "$secrets"
  fi

  # Execute the tool
  exec "$tool_name" "$@"
''
