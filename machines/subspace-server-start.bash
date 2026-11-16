# Starts the subspace server for the custom.subspace-server user service.
# machines/subspace-server.nix wraps this file with writeShellApplication,
# which adds the shebang, `set -euo pipefail`, and PATH.
#
# Environment:
#   SUBSPACE_DATA_DIR, SUBSPACE_WS_HOST, SUBSPACE_WS_PORT
#       Passed on to the server.
#   WLR_SUBSPACE_ENV_FILE
#       Optional. Bash sources this file with `set -a` before anything else,
#       so it can set GITHUB_TOKEN and other secrets, also by command
#       substitution.
#   WLR_SUBSPACE_GH
#       Optional path to gh. If GITHUB_TOKEN is not set after the
#       environment file, `gh auth token` sets it.

log() {
    printf 'subspace-server-start: %s\n' "$*" >&2
}

if [[ -n "${WLR_SUBSPACE_ENV_FILE:-}" ]]; then
    if [[ ! -r "$WLR_SUBSPACE_ENV_FILE" ]]; then
        log "cannot read $WLR_SUBSPACE_ENV_FILE"
        exit 1
    fi
    set -a
    # shellcheck disable=SC1090
    source "$WLR_SUBSPACE_ENV_FILE"
    set +a
fi

# Read the token at each start, so that a new gh login takes effect at the
# next restart. A failure exits, because the keyring can still be locked; the
# service manager then tries again.
if [[ -n "${WLR_SUBSPACE_GH:-}" && -z "${GITHUB_TOKEN:-}" ]]; then
    if ! GITHUB_TOKEN=$("$WLR_SUBSPACE_GH" auth token); then
        log "cannot get a GitHub token from gh: log in with 'gh auth login', or set custom.subspace-server.githubTokenFromGh = false"
        exit 1
    fi
    export GITHUB_TOKEN
fi

exec subspace-server
