#!/bin/bash

# On good operating systems, .bash_profile is run on login shells only. It is best used to set
# environment variables (e.g. PATH modifications), since these are inherited by child processes.
# .bashrc is run on each new shell. It is best used to set aliases and define functions, since
# these are NOT inherited by child processes.

# On macOS, .bash_profile is run for each new GUI terminal (rather than on login), but child
# invocations of bash, such as by "exec bash", will still run .bashrc.

# It is common, but not universal, practice for one of .bash_profile and .bashrc to source the
# other one. It seems to me that the sensible approach is for .bash_profile to source .bashrc so
# that aliases and function definitions are available in login shells. Unfortunately, that's not
# necessarily the direction that all systems take.

# Because it's hard to predict when either of these will be run, this script is intended to be
# sourced by BOTH .bashrc and .bash_profile. It uses the WLR_ENV_BASH environment variable to avoid
# modifying the environment twice. In the not-altogether-unlikely event that it is sourced multiple
# times, it will redefine aliases and functions, but this is harmless.

# Because this script deliberately avoids making repeated alterations to the environment, it should
# be noted that if child commands (think tmux, venv, etc) also modify the PATH, these new
# modifications can take precedence even if those children hand off to another instance of bash.
# For example, if a Python script running in a venv calls a bash script, that bash script will see
# Python from the venv, not Python from pyenv or the system.


[ -n "$__ALIAS_STACK" ] && exit # xonsh source-bash or similar

if [ -d "$PWD" ] && ! [ -x "$PWD" ] && [ "$PWD" != "$HOME" ]; then
    echo "$PWD not navigable, changing to $HOME" >&2
    cd "$HOME"
fi


good_steps=()
warnings=()
err_steps=()

print_status() {
    if [ "${#good_steps[@]}" -gt 0 ]; then
        wlr-good "${good_steps[@]}"
    fi
    for warning in "${warnings[@]}"; do
        wlr-warn "$warning"
    done
    if [ "${#err_steps[@]}" -gt 0 ]; then
        wlr-err "${err_steps[@]}"
    fi
    good_steps=()
    warnings=()
    err_steps=()
}

wlr_interactive=
if [ -n "$TERM" ]; then
    case "$-" in
        *i*)
            wlr_interactive=y
            ;;
    esac
fi
[ -n "$INTELLIJ_ENVIRONMENT_READER" ] || [ -n "$CLAUDECODE" ] && wlr_interactive=

try_source() {
    if [ -r "$1" ]; then
        . "$1"
    else
        return 1
    fi
}


ensurevar() {
    var=
    require_dir=
    require_file=
    require_exists=
    head=
    while [ "$#" -gt 0 ]; do
        shift
        if [ "$1" == '--head' ]; then
            head=y
        elif [ "$1" == '--require-dir' ]; then
            require_dir=y
        elif [ "$1" == '--require-file' ]; then
            require_file=y
        elif [ "$1" == '--require-exists' ]; then
            require_exists=y
        elif [ "$1" == '' ]; then
            continue
        elif [ -z "$var" ]; then
            var="$1"
        elif [ -n "$require_exists" ] && ! [ -e "$1" ]; then
            [ -n "$wlr_interactive" ] && printf 'Warning: tried to add %s to %s, but it does not exist\n' "$1" "$var" >&2
        elif [ -n "$require_dir" ] && ! [ -d "$1" ]; then
            [ -n "$wlr_interactive" ] && printf 'Warning: tried to add %s to %s, but it is not a directory\n' "$1" "$var" >&2
        elif [ -n "$require_file" ] && ! [ -f "$1" ]; then
            [ -n "$wlr_interactive" ] && printf 'Warning: tried to add %s to %s, but it is not a file\n' "$1" "$var" >&2
        elif ! echo "${!var}" | grep -Eq "(^|:)$1($|:)"; then
            if [ -n "$head" ]; then
                printf -v "$var" '%s' "$1:${!var}"
            else
                printf -v "$var" '%s' "${!var}:$1"
            fi
            export "$var"
        fi
    done
}


# TODO when confident in ensurevar, rewrite this as ensurevar PATH "$@"
ensurepath() {
    head=
    if [ "$1" == "--head" ]; then
        head=y
        shift
    fi
    for t in "$@"; do
        if ! [ -d "$t" ]; then
            warnings+=("Warning: tried to add $t to PATH, but it is not a directory")
            continue
        fi
        if ! echo "$PATH" | grep -Eq "(^|:)$t($|:)"; then
            if [ -n "$head" ]; then
                export PATH="$t:$PATH"
            else
                export PATH="$PATH:$t"
            fi
        fi
    done
}


# brew

if [ -x "/opt/homebrew/bin/brew" ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi


# BSD coreutils suck, add gnubin to path
# https://apple.stackexchange.com/a/371984
# must be run early because new macOS doesn't even have realpath (!)

if command -v brew >/dev/null 2>&1; then
    HOMEBREW_PREFIX="$(brew --prefix)"
    for gnubin_dir in "$HOMEBREW_PREFIX"/opt/*/libexec/gnubin; do
        if [ "$gnubin_dir" == "$HOMEBREW_PREFIX"'/opt/*/libexec/gnubin' ]; then
            continue # glob failed to expand
        fi
        ensurepath --head "$gnubin_dir"
    done
fi



# early environment setup

if [ -z "$WLR_UNALIASED_PATH" ]; then
    export WLR_UNALIASED_PATH="$PATH"
fi

if [ -z "$WLR_ENV_PATH" ]; then
    export WLR_ENV_PATH="$HOME/.wlrenv"
    [ -n "$BASH_SOURCE" ] && export WLR_ENV_PATH="$(realpath "${BASH_SOURCE%/*}")"
fi

ensurepath "$WLR_ENV_PATH/bin/early"

# https://unix.stackexchange.com/a/9607
wlr_detect_ssh() {
    if [ -n "$SSH_CLIENT" ] || [ -n "$SSH_CONNECTION" ] || [ -n "$SSH_TTY" ]; then
        return 0
    else
        case $(ps -o comm= -p "$PPID") in
            sshd|*/sshd)
                return 0
                ;;
        esac
    fi
    return 1
}

wlr_detect_named_term() {
    if [ -n "$__INTELLIJ_COMMAND_HISTFILE__" ]; then
        {
            pwd
            echo "$__INTELLIJ_COMMAND_HISTFILE__"
        } | sha256sum | cut -c1-7
        return 0
    elif [ -n "$VSCODE_SHELL_INTEGRATION" ] || [ -n "$ZED_TERM" ]; then
        pwd | sha256sum | cut -c1-7
        return 0
    fi
    return 1
}

wlr_suspect_tty() {
    if [ -n "$WLR_FIRST_SHELL" ]; then
        return 0
    fi
    # usually this is from IDEs' integrated terminals
    if [ "$(realpath "$(pwd)")" != "$(realpath "$HOME")" ]; then
        return 0
    fi
    if [ -n "$DISPLAY" ] || wlr_detect_ssh || [ "$(uname)" == 'Darwin' ]; then
        return 1
    fi
    case "$(tty)" in
        /dev/tty*)
            return 0
            ;;
    esac
    return 1
}

# invoke zellij/tmux, if applicable

if [ -n "$wlr_interactive" ]; then
    if [ -n "$ZELLIJ" ]; then
        good_steps+=('zellij')
    elif [ -n "$TMUX" ]; then
        good_steps+=('tmux')
    elif [ "$WLR_TMUX" == 'n' ]; then
        warnings+=('tmux - skipping (disabled)')
    elif term="$(wlr_detect_named_term)"; then
        print_status
        exec tmux new-session -A -s "$term" -c "$(pwd)"
    elif wlr_suspect_tty; then
        warnings+=('tmux - skipping (suspect tty)')
    elif command -v zellij >/dev/null 2>&1 && false; then # TODO reenable
        print_status
        exec zellij options --disable-mouse-mode
    elif command -v tmux >/dev/null 2>&1; then
        print_status
        exec tmux new-session
    else
        err_steps+=('tmux/zellij')
    fi
fi


# enable checkwinsize (default on most systems, but not macOS)

shopt -s checkwinsize


# on macOS, use SSH_AUTH_SOCK_LOCAL over SSH_AUTH_SOCK if present

if [ -n "$SSH_AUTH_SOCK_LOCAL" ] && [ "$(uname)" == 'Darwin' ]; then
    export SSH_AUTH_SOCK="$SSH_AUTH_SOCK_LOCAL"
fi


# load env vars from .wlrenv/env, .config/env, and .config/env_secret

try_source "$WLR_ENV_PATH/env"
try_source "${XDG_CONFIG_HOME:-$HOME/.config}/env"
try_source "${XDG_CONFIG_HOME:-$HOME/.config}/env_secret"


# check for update, at most once an hour

if [ -n "$wlr_interactive" ]; then
    if "$WLR_ENV_PATH/bin/meta/wlr-check-update"; then
        . "$WLR_ENV_PATH/env.bash"
        return
    fi
fi


# initialize env shims

wlr_check_env_shim() {
    if ! [ -f "$HOME/.$1/shims/$2" ]; then
        warnings+=("$1: $2 shim is missing ($1 install <version> and/or $1 rehash to fix)")
        return 1
    fi
}

# set up fnm
if command -v fnm >/dev/null 2>&1 && eval "$(fnm env --shell bash)"; then
    good_steps+=('fnm')
else
    err_steps+=('fnm')
fi

# disabled while I figure out the correct way to approach conda envs
# looks like some pyenv-related tools might be able to handle it?
wlr_setup_conda() {
    if ! command -v conda >/dev/null 2>&1; then
        err_steps+=('conda')
        return
    fi
    if ! [ -d "$HOME/.conda/envs/main" ]; then
        warnings+=('conda is installed, but main env is missing (conda create -n main to fix)')
        return
    fi
    if eval "$(conda shell.posix activate main)"; then
        good_steps+=('conda')
        return
    fi
    err_steps+=('conda')
}
# disabled by default while I figure out the correct way to approach conda envs
# maybe some pyenv-related tools can handle it?
# would like to have shims for conda env binaries (e.g. cadquery GUI)
# unsure how to handle python scripts that need a conda env (e.g. cadquery stl generator scripts)
[ -n "$WLR_CONDA" ] && wlr_setup_conda
unset wlr_setup_conda

wlr_setup_krew() {
    if ! command -v kubectl >/dev/null 2>&1; then
        err_steps+=('kubectl')
        return
    elif ! command -v kubectl-krew >/dev/null 2>&1; then
        err_steps+=('kubectl-krew')
        return
    elif ! [ -d "$HOME/.krew/bin" ]; then
        warnings+=('kubectl-krew is installed, but bin dir is missing')
        return
    else
        ensurepath "$HOME/.krew/bin"
        good_steps+=('kubectl-krew')
    fi
}
wlr_setup_krew
unset wlr_setup_krew


# initialize PATH for custom aliases, wrappers, and scripts
ensurepath "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/.ghcup/bin" 2>/dev/null # allow these to fail silently, they might not exist
while IFS=$'\n' read wlr_env_subdir; do
    ensurepath "$wlr_env_subdir"
done < <(find "$WLR_ENV_PATH/bin" -mindepth 1 -maxdepth 1 -type d -not -name .git)
unset wlr_env_subdir


# push env into dbus and systemd
if [ -n "$wlr_interactive" ]; then
    if command -v dbus-update-activation-environment >/dev/null 2>&1; then
        dbus-update-activation-environment --systemd --all
    elif command -v systemctl >/dev/null 2>&1; then
        systemctl --user import-environment
    fi
fi


# do host-specific autorun
if [ -n "$WLR_FIRST_SHELL" ]; then
    export WLR_FIRST_SHELL=
    if [ -x "$WLR_ENV_PATH/autorun/$HOSTNAME" ]; then
        exec "$WLR_ENV_PATH/autorun/$HOSTNAME"
    fi
fi


if [ -n "$wlr_interactive" ]; then
    # print env report
    wlr-check-env

    # invoke xonsh, if applicable
    if [ "$WLR_XONSH" == 'n' ] || [ "$POETRY_ACTIVE" == '1' ]; then
        warnings+=('xonsh - skipping')
    elif command -v uv >/dev/null 2>&1; then
        print_status
        wlr-working 'xonsh'
        uv tool install "$WLR_ENV_PATH/" --quiet
        export WLR_XONSH='n'  # avoid reentrancy on further executions of bash
        export XONSHRC="$WLR_ENV_PATH/xonshrc.py"
        exec xonsh
    elif command -v xonsh >/dev/null 2>&1; then
        print_status
        wlr-working 'xonsh'
        export WLR_XONSH='n'  # avoid reentrancy on further executions of bash
        export XONSHRC="$WLR_ENV_PATH/xonshrc.py"
        exec xonsh
    else
        err_steps+=('xonsh')
    fi
fi


# initialize completions and corrections

try_source /usr/share/git/completion/git-completion.bash || \
    try_source /usr/local/etc/profile.d/bash_completion.sh
try_source /usr/share/git/completion/git-prompt.sh
try_source /usr/share/doc/pkgfile/command-not-found.bash

if command -v thefuck >/dev/null 2>&1; then
    eval "$(thefuck --alias)"
    good_steps+=('thef***')
else
    err_steps+=('thef****')
fi


# set up prompt

export PS1='\[\033[31m<\]\n\[\033[0G\033[m\]$(foo=$?; [ $foo -gt 0 ] && printf "[%s]" $foo; unset foo)\[\033[0;31m\]\t\[\033[0;32m\]\u@\h\[\033[0;34m\]\w\[\033[m\]\$ '
export PS4='$(foo=$?; [ $foo -gt 0 ] && printf "[%s]" $foo; unset foo)+ ${FUNCNAME[0]:+${FUNCNAME[0]}():}line ${LINENO}: '

if command -v __git_ps1 >/dev/null 2>&1; then
    export PS1='\[\033[31m<\]\n\[\033[0G\033[m\]$(foo=$?; [ $foo -gt 0 ] && printf "[%s]" $foo; unset foo)\[\033[0;31m\]\t\[\033[0;32m\]\u@\h\[\033[0;34m\]\w\[\033[0;33m\]$(__git_ps1 "(%s)")\[\033[m\]\$ '
fi


# use aliases to resolve naming conflicts and overwrite default behaviour

alias gap='git add -p' # some algebra package
alias gm='git merge' # graphicsmagick
alias gs='git status' # ghostscript

command -v grmx > /dev/null 2>&1 && alias grm='grmx' # macos, with brew: gnu rm

command -v bat >/dev/null 2>&1 && alias cat='bat'
if command -v eza >/dev/null 2>&1; then
    alias ls='eza'
else
    command -v exa >/dev/null 2>&1 && alias ls='exa'
fi
command -v nvim >/dev/null 2>&1 && alias vim='nvim'

command -v dd-shim >/dev/null 2>&1 && alias dd='dd-shim'
command -v gradle-shim >/dev/null 2>&1 && alias gradle='gradle-shim'
command -v rsync-shim >/dev/null 2>&1 && alias rsync='rsync-shim'
command -v yay-shim >/dev/null 2>&1 && alias yay='yay-shim'

command -v k9sx > /dev/null 2>&1 && alias k9s='k9sx'

command -v sshx > /dev/null 2>&1 && alias ssh='sshx'
command -v sshfsx > /dev/null 2>&1 && alias sshfs='sshfsx'
command -v moshx > /dev/null 2>&1 && alias mosh='moshx'

__cd() {
    if [ $# -eq 0 ]; then
        popd >/dev/null 2>&1
    else
        pushd "$1" >/dev/null
    fi
    [ -n "$FNM_MULTISHELL_PATH" ] && fnm use --silent-if-unchanged
}
alias cd='__cd'

mkcd() {
    if [ $# -ne 1 ]; then
        printf 'Usage: mkcd DIRECTORY\n' >&2
        return 1
    fi
    mkdir "$1"
    pushd "$1" >/dev/null
}


[ -n "$wlr_interactive" ] && print_status


unset good_steps
unset warnings
unset err_steps
unset print_status
unset try_source
unset ensurepath
unset wlr_detect_ssh
unset wlr_suspect_tty
unset wlr_check_env_shim
