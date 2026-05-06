{
  pkgs,
  backend ? "pass",
}:

pkgs.writeShellScriptBin "secwrap" ''
    set -euo pipefail

    usage() {
        cat >&2 <<'USAGE'
  secwrap - wrap commands with secrets from pass/passage

  Usage: secwrap [options] <command> [args...]

  Looks up "config/env/<command>" in the configured password store.
  If found, parses KEY=VALUE lines and exports them before exec'ing
  the command. If not found, exec's the command directly.

  Options (must appear before <command>):
    --from <name>   Load secrets for <name> instead of <command>
    --list          List tool names that have entries under config/env/
    --help          Show this help message
  USAGE
    }

    list_tools() {
        local store_dir ext
        case "${backend}" in
            passage)
                store_dir="''${PASSAGE_DIR:-$HOME/.passage/store}"
                ext="age"
                ;;
            *)
                store_dir="''${PASSWORD_STORE_DIR:-$HOME/.password-store}"
                ext="gpg"
                ;;
        esac
        local env_dir="$store_dir/config/env"
        [[ -d "$env_dir" ]] || return 0
        shopt -s nullglob
        local f base
        for f in "$env_dir"/*."$ext"; do
            base="''${f##*/}"
            printf '%s\n' "''${base%.$ext}"
        done
    }

    from_name=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help)
                usage
                exit 0
                ;;
            --list)
                list_tools
                exit 0
                ;;
            --from)
                if [[ $# -lt 2 ]]; then
                    echo "secwrap: --from requires an argument" >&2
                    exit 1
                fi
                from_name="$2"
                shift 2
                ;;
            -*)
                echo "secwrap: unknown option: $1" >&2
                usage
                exit 1
                ;;
            *)
                break
                ;;
        esac
    done

    if [[ $# -lt 1 ]]; then
        usage
        exit 1
    fi

    tool_name="$1"
    shift

    secret_key="''${from_name:-$tool_name}"
    secret_path="config/env/''${secret_key}"

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

    # Execute the command
    exec "$tool_name" "$@"
''
