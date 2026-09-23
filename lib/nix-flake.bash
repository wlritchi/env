# Shared setup for scripts that run tools from the wlrenv flake.
#
# Source this file, then call `wlr_nix_flake_setup "$@"`. It consumes leading
# `--override-input INPUT FLAKEREF` options, reads the per-machine overrides
# file, and leaves the remaining arguments in `wlr_nix_flake_args`. After
# setup, `wlr_nix_flake_run TOOL SUBCOMMAND [ARG]...` runs TOOL from the
# committed public flake against the correct flake reference.
#
# Variables set by wlr_nix_flake_setup:
#   wlr_nix_flake_public_ref  git+file:// reference to the committed public HEAD
#   wlr_nix_flake_ref         flake to evaluate (private overlay if present)
#   wlr_nix_flake_path        directory of that flake (also the new cwd)
#   wlr_nix_flake_overrides   --override-input arguments for the flake tools
#   wlr_nix_flake_args        arguments that were not consumed

wlr_nix_flake_usage_overrides() {
    cat <<'USAGE'
INPUT names a public flake input (for example `ccpatch`). FLAKEREF replaces
it for this run only. Persistent per-machine overrides go in
  ${XDG_CONFIG_HOME:-~/.config}/wlrenv/nix-input-overrides
with one `INPUT=FLAKEREF` per line (`#` starts a comment; a leading `~/`
expands to $HOME). A local git checkout is best given as a git+file:// ref,
which follows .gitignore and works with fsmonitor enabled:
  ccpatch=git+file:///home/me/ccpatch
USAGE
}

# Get the short hostname with a fallback chain.
wlr_nix_flake_short_hostname() {
    local hn
    if command -v hostname >/dev/null 2>&1; then
        hn=$(hostname -s 2>/dev/null) && [ -n "$hn" ] && echo "$hn" && return 0
    fi
    if hn=$(uname -n 2>/dev/null) && [ -n "$hn" ]; then
        echo "${hn%%.*}"
        return 0
    fi
    if [ -f /proc/sys/kernel/hostname ]; then
        hn=$(cat /proc/sys/kernel/hostname 2>/dev/null) && [ -n "$hn" ] && echo "${hn%%.*}" && return 0
    fi
    return 1
}

wlr_nix_flake_setup() {
    if ! command -v nix >/dev/null 2>&1; then
        wlr-err "Nix not installed"
        return 1
    fi

    # Collect input overrides as `INPUT=FLAKEREF` pairs: the config file first,
    # then the command line.
    local overrides_file="${XDG_CONFIG_HOME:-$HOME/.config}/wlrenv/nix-input-overrides"
    local input_overrides=()
    local line
    if [[ -f "$overrides_file" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            line="${line%%#*}"
            line="${line#"${line%%[![:space:]]*}"}"
            line="${line%"${line##*[![:space:]]}"}"
            [[ -z "$line" ]] && continue
            if [[ "$line" != *=* ]]; then
                wlr-err "$overrides_file: expected INPUT=FLAKEREF, got: $line"
                return 1
            fi
            input_overrides+=("$line")
        done < "$overrides_file"
    fi
    while [[ $# -gt 0 && "$1" == --override-input ]]; do
        if [[ $# -lt 3 ]]; then
            wlr-err "--override-input needs INPUT and FLAKEREF"
            return 1
        fi
        input_overrides+=("$2=$3")
        shift 3
    done
    wlr_nix_flake_args=("$@")

    # Use one committed public revision for the configuration and tool launchers.
    local public_rev
    public_rev="$(git -C "$WLR_ENV_PATH" rev-parse --verify 'HEAD^{commit}')"
    wlr_nix_flake_public_ref="git+file://$WLR_ENV_PATH?rev=$public_rev"

    # Keep private working-tree changes visible without updating its lock file.
    local private_env_path="$HOME/.wlrenv-private"
    local input_prefix=""
    wlr_nix_flake_overrides=()
    if [[ -d "$private_env_path" ]]; then
        wlr_nix_flake_path="$private_env_path"
        wlr_nix_flake_ref="$private_env_path"
        wlr_nix_flake_overrides=(--override-input wlrenv "$wlr_nix_flake_public_ref")
        # Public inputs are nested under the overlay's `wlrenv` input.
        input_prefix="wlrenv/"
    else
        wlr_nix_flake_path="$WLR_ENV_PATH"
        wlr_nix_flake_ref="$wlr_nix_flake_public_ref"
    fi

    # Overrides make the result differ from flake.lock, so always say so.
    local override input_name input_ref
    for override in ${input_overrides[@]+"${input_overrides[@]}"}; do
        input_name="${override%%=*}"
        input_ref="${override#*=}"
        input_ref="${input_ref/#\~\//$HOME/}"
        wlr-warn "overriding flake input $input_name with $input_ref"
        wlr_nix_flake_overrides+=(--override-input "$input_prefix$input_name" "$input_ref")
    done

    cd "$wlr_nix_flake_path"

    export NIX_CONFIG="experimental-features = nix-command flakes"

    # Export the hostname for impure flake evaluation.
    export NIX_HOSTNAME
    NIX_HOSTNAME="$(wlr_nix_flake_short_hostname)" || NIX_HOSTNAME="default"
}

# Run TOOL from the public flake's own outputs, so the tool version always
# matches the inputs locked in flake.lock.
wlr_nix_flake_run() {
    local tool="$1"
    shift
    nix run --impure "$wlr_nix_flake_public_ref#$tool" -- "$@" \
        --impure --flake "$wlr_nix_flake_ref#default" \
        ${wlr_nix_flake_overrides[@]+"${wlr_nix_flake_overrides[@]}"}
}
