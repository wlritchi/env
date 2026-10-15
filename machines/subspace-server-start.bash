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
#   WLR_SUBSPACE_RELAY_COMPAT
#       1 keeps `notifications` relays that use their default token path
#       working: data of the old notifications daemon moves from
#       ~/.claude/notifications on the first start, and
#       ~/.claude/notifications/token becomes a link to the server's token.

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

data_dir="$SUBSPACE_DATA_DIR"
legacy_dir="$HOME/.claude/notifications"

# Replace LINK with a symlink to TARGET in one rename, so that a relay never
# sees the path missing and creates a token of its own.
link_atomically() {
    local target="$1" link="$2"
    ln -s "$target" "$link.tmp.$$"
    mv -f "$link.tmp.$$" "$link"
}

dir_is_empty() {
    [[ ! -e "$1" ]] || [[ -z "$(ls -A "$1")" ]]
}

# The old daemon kept its state next to the token. A token alone can come
# from a relay, so it does not count as daemon state.
has_legacy_state() {
    [[ -d "$legacy_dir" && ! -L "$legacy_dir" ]] || return 1
    local entry
    for entry in "$legacy_dir"/* "$legacy_dir"/.[!.]*; do
        [[ -e "$entry" || -L "$entry" ]] || continue
        [[ "${entry##*/}" == token ]] && continue
        return 0
    done
    return 1
}

port_in_use() {
    (exec 3<>"/dev/tcp/$SUBSPACE_WS_HOST/$SUBSPACE_WS_PORT") 2>/dev/null
}

migrate_legacy_data() {
    if port_in_use; then
        log "port $SUBSPACE_WS_PORT is in use, so the old notifications daemon can still be running"
        log "stop it (for example: systemctl --user disable --now notifications-daemon), then restart this service"
        exit 1
    fi
    log "moving the old notifications daemon data from $legacy_dir to $data_dir"
    subspace-migrate-from-notifications
}

link_relay_token() {
    local token="$data_dir/token" legacy_token="$legacy_dir/token"
    if [[ -L "$legacy_token" ]]; then
        return 0
    fi
    if [[ -f "$legacy_token" ]]; then
        if [[ ! -e "$token" ]]; then
            # A relay made this token before the server first ran. The
            # server takes it, so that running relays stay authenticated.
            log "using the relay token $legacy_token as the server token"
            install -d -m 700 "$data_dir"
            install -m 600 "$legacy_token" "$token"
        elif ! cmp -s "$legacy_token" "$token"; then
            log "warning: $legacy_token is not the server token $token"
            log "warning: relays that read it will be refused; remove it to link it to the server token"
            return 0
        fi
    else
        if [[ ! -e "$token" ]]; then
            install -d -m 700 "$data_dir"
            (
                set -C
                umask 077
                head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' >"$token"
            )
        fi
        install -d -m 700 "$legacy_dir"
    fi
    log "linking $legacy_token to $token"
    link_atomically "$token" "$legacy_token"
}

if [[ "${WLR_SUBSPACE_RELAY_COMPAT:-0}" == 1 ]] &&
    [[ "$(realpath -m "$data_dir")" != "$(realpath -m "$legacy_dir")" ]]; then
    if has_legacy_state && dir_is_empty "$data_dir"; then
        migrate_legacy_data
    fi
    if [[ -z "${SUBSPACE_TOKEN:-}" ]]; then
        link_relay_token
    fi
fi

exec subspace-server
