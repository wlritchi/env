# Starts `hyperspace launcher --stay` for the custom.hyperspace-launcher user
# service. machines/hyperspace-launcher.nix wraps this file with
# writeShellApplication, which adds the shebang and `set -euo pipefail`.
#
# The sessions that the launcher resumes get this environment through
# `claude --bg`.
#
# Environment:
#   WLR_HYPERSPACE
#       Path to the hyperspace client.
#   WLR_HYPERSPACE_CLAUDE_ARGS_FILE
#       Optional. A JSON list of Claude Code options, which becomes
#       HYPERSPACE_LAUNCHER_CLAUDE_ARGS.
#   WLR_HYPERSPACE_ENV_FILE
#       Optional. Bash sources this file with `set -a` before the launcher
#       starts.

log() {
    printf 'hyperspace-launcher-start: %s\n' "$*" >&2
}

# launchd gives a minimal PATH. The launcher finds `claude` on PATH, and the
# resumed sessions use the same PATH.
for dir in "$HOME/.local/bin" "$HOME/.nix-profile/bin"; do
    case ":$PATH:" in
        *":$dir:"*) ;;
        *) PATH="$dir:$PATH" ;;
    esac
done
export PATH

if [[ -n "${WLR_HYPERSPACE_CLAUDE_ARGS_FILE:-}" ]]; then
    HYPERSPACE_LAUNCHER_CLAUDE_ARGS=$(<"$WLR_HYPERSPACE_CLAUDE_ARGS_FILE")
    export HYPERSPACE_LAUNCHER_CLAUDE_ARGS
fi

if [[ -n "${WLR_HYPERSPACE_ENV_FILE:-}" ]]; then
    if [[ ! -r "$WLR_HYPERSPACE_ENV_FILE" ]]; then
        log "cannot read $WLR_HYPERSPACE_ENV_FILE"
        exit 1
    fi
    set -a
    # shellcheck disable=SC1090
    source "$WLR_HYPERSPACE_ENV_FILE"
    set +a
fi

if [[ ! -x "$WLR_HYPERSPACE" ]]; then
    log "cannot run $WLR_HYPERSPACE: install the hyperspace client, or set custom.hyperspace-launcher.executable"
    exit 1
fi

exec "$WLR_HYPERSPACE" --quiet launcher --stay
