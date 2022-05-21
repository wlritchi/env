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


RECOMMENDED_XONSH_VERSION=xonsh/0.12.4


[ -n "$__ALIAS_STACK" ] && exit # xonsh source-bash or similar


wlr_interactive=
if [ -n "$TERM" ]; then
    case "$-" in
        *i*)
            wlr_interactive=y
            ;;
    esac
fi
[ -n "$INTELLIJ_ENVIRONMENT_READER" ] && wlr_interactive=


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
            [ -n "$wlr_interactive" ] && printf 'Warning: tried to add %s to PATH, but it is not a directory\n' "$t" >&2
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
    fi
    return 1
}

wlr_suspect_tty() {
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
        wlr-good 'zellij'
    elif [ -n "$TMUX" ]; then
        # make sure hot-spares exists and top it up to 3 windows
        tmux new-session -d -s hot-spares >/dev/null 2>&1 || true
        [ "$(tmux list-windows -t hot-spares | wc -l)" -lt 3 ] && tmux new-window -t hot-spares:
        wlr-good 'tmux'
    elif [ "$WLR_TMUX" == 'n' ]; then
        wlr-warn 'tmux - skipping'
    elif term="$(wlr_detect_named_term)"; then
        exec tmux new-session -A -s "$term" -c "$(pwd)"
    elif wlr_suspect_tty; then
        wlr-warn 'tmux - skipping'
    elif command -v zellij >/dev/null 2>&1 && false; then # TODO reenable
        exec zellij options --disable-mouse-mode
    elif command -v tmux >/dev/null 2>&1; then
        # make sure hot spares session exists
        tmux new-session -d -s hot-spares 2>/dev/null || true
        # tmux will be executing its own commands in a temporary window
        # move that to window 1, then move a hot spare into the current window (0)
        # this preserves default new-session behaviour that the shell starts at window 0
        # finally, we recreate hot-spares (if we destroyed it), and add a window to it to replace the one we took
        exec tmux new-session 'tmux move-window \; move-window -s hot-spares \; if-shell "tmux new-session -d -s hot-spares" "run-shell true" \; new-window -t hot-spares:'
    else
        wlr-err 'tmux'
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
        wlr-warn "$1 is installed, but $2 shim is missing ($1 install <version> and/or $1 rehash to fix)"
        return 1
    fi
}

wlr_setup_pyenv() {
    if command -v pyenv >/dev/null 2>&1; then
        if ! pyenv --version | grep -q 'pyenv 1'; then
            # pyenv 2+ splits init into two parts
            # "pyenv init --path" updates PATH (intended for .bash_profile)
            # "pyenv init -" sets functions, completions, etc (also curiously PYENV_SHELL env var still lives here)
            [ -z "$WLR_ENV_BASH" ] && eval "$(pyenv init --path)"
        fi
        eval "$(pyenv init -)"
        if ! command -v pyenv-virtualenv >/dev/null 2>&1; then
            [ -n "$wlr_interactive" ] && wlr-warn 'pyenv is installed, but pyenv-virtualenv was not found'
            return
        fi
        eval "$(pyenv virtualenv-init -)"
        [ -z "$wlr_interactive" ] && return
        wlr_check_env_shim pyenv python && \
            wlr_check_env_shim pyenv pip && \
            wlr-good 'pyenv'
    elif [ -z "$wlr_interactive" ]; then
        return
    elif command -v python >/dev/null 2>&1; then
        wlr-warn 'python is installed, but pyenv was not found'
    else
        wlr-err 'python'
    fi
}
[ "$WLR_PYENV" != 'n' ] && wlr_setup_pyenv
unset wlr_setup_pyenv

wlr_setup_nodenv() {
    if command -v nodenv >/dev/null 2>&1; then
        eval "$(nodenv init -)"
        [ -z "$wlr_interactive" ] && return
        if command -v nvm >/dev/null 2>&1; then
            wlr-warn 'nodenv is installed, but nvm is also present (you should uninstall nvm)'
        fi
        if ! command -v node-build >/dev/null 2>&1; then
            wlr-warn 'nodenv is installed, but node-build is missing (install nodenv-node-build-git to fix)'
        fi
        wlr_check_env_shim nodenv node && \
            wlr_check_env_shim nodenv npm && \
            wlr_check_env_shim nodenv npx && \
            wlr-good 'nodenv'
    elif [ -z "$wlr_interactive" ]; then
        return
    elif command -v node >/dev/null 2>&1; then
        wlr-warn 'node is installed, but nodenv was not found'
    else
        wlr-err 'node'
    fi
}
[ "$WLR_NODENV" != 'n' ] && wlr_setup_nodenv
unset wlr_setup_nodenv

wlr_check_pipx() {
    [ -z "$wlr_interactive" ] && return
    if command -v pipx >/dev/null 2>&1; then
        wlr-good 'pipx'
    else
        wlr-err 'pipx'
    fi
}
wlr_check_pipx
unset wlr_check_pipx

# disabled while I figure out the correct way to approach conda envs
# looks like some pyenv-related tools might be able to handle it?
wlr_setup_conda() {
    if ! command -v conda >/dev/null 2>&1; then
        [ -n "$wlr_interactive" ] && wlr-err 'conda'
        return
    fi
    if ! [ -d "$HOME/.conda/envs/main" ]; then
        [ -n "$wlr_interactive" ] && wlr-warn 'conda is installed, but main env is missing (conda create -n main to fix)'
        return
    fi
    if eval "$(conda shell.posix activate main)"; then
        [ -n "$wlr_interactive" ] && wlr-good 'conda'
        return
    fi
    [ -n "$wlr_interactive" ] && wlr-err 'conda'
}
# disabled by default while I figure out the correct way to approach conda envs
# maybe some pyenv-related tools can handle it?
# would like to have shims for conda env binaries (e.g. cadquery GUI)
# unsure how to handle python scripts that need a conda env (e.g. cadquery stl generator scripts)
[ -n "$WLR_CONDA" ] && wlr_setup_conda
unset wlr_setup_conda

wlr_setup_krew() {
    if ! command -v kubectl >/dev/null 2>&1; then
        [ -n "$wlr_interactive" ] && wlr-err 'kubectl'
        return
    elif ! command -v kubectl-krew >/dev/null 2>&1; then
        [ -n "$wlr_interactive" ] && wlr-err 'kubectl-krew'
        return
    elif ! [ -d "$HOME/.krew/bin" ]; then
        [ -n "$wlr_interactive" ] && wlr-warn 'kubectl-krew is installed, but bin dir is missing'
        return
    else
        ensurepath "$HOME/.krew/bin"
        [ -n "$wlr_interactive" ] && wlr-good 'kubectl-krew'
    fi
}
wlr_setup_krew
unset wlr_setup_krew

# BSD coreutils suck, add gnubin to path
# https://apple.stackexchange.com/a/371984

if command -v brew >/dev/null 2>&1; then
    HOMEBREW_PREFIX="$(brew --prefix)"
    for gnubin_dir in "$HOMEBREW_PREFIX"/opt/*/libexec/gnubin; do
        if [ "$gnubin_dir" == "$HOMEBREW_PREFIX"'/opt/*/libexec/gnubin' ]; then
            continue # glob failed to expand
        fi
        ensurepath --head "$gnubin_dir"
    done
fi


# initialize PATH for custom aliases, wrappers, and scripts

ensurepath "$HOME/.local/bin" "$HOME/.cargo/bin" 2>/dev/null # allow these to fail silently, they might not exist
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


# create terminal cgroup, if applicable

if [ -n "$wlr_interactive" ]; then
    if command -v tcg >/dev/null 2>&1; then
        cgroup="$(tcg create)"
        wlr-good tcg - "$cgroup"
        if [ -n "$TMUX" ] && command -v tmux >/dev/null 2>&1; then
            tmux rename-window "$cgroup"
        fi
    else
        wlr-err 'tcg'
    fi
fi


# invoke xonsh, if applicable

if [ -n "$wlr_interactive" ]; then
    if [ "$WLR_XONSH" == 'n' ] || [ "$POETRY_ACTIVE" == '1' ]; then
        wlr-warn 'xonsh - skipping'
    elif command -v xonsh >/dev/null 2>&1; then
        wlr-working 'xonsh'
        xonsh_version="$(xonsh --version)"
        if [ "$xonsh_version" != "$RECOMMENDED_XONSH_VERSION" ]; then
            wlr-warn "xonsh version $xonsh_version is installed (recommended version is $RECOMMENDED_XONSH_VERSION)"
        fi
        export WLR_BASH_BIN="$(which bash)"
        exec xonsh
    elif command -v pipx >/dev/null 2>&1; then
        wlr-err 'xonsh is not installed (but you can install it with `pipx install xonsh`'
    else
        wlr-err 'xonsh'
    fi
fi


# initialize completions and corrections

try_source /usr/share/git/completion/git-completion.bash || \
    try_source /usr/local/etc/profile.d/bash_completion.sh
try_source /usr/share/git/completion/git-prompt.sh
try_source /usr/share/doc/pkgfile/command-not-found.bash

if command -v register-python-argcomplete >/dev/null 2>&1; then
    command -v pipx >/dev/null 2>&1 && eval "$(register-python-argcomplete pipx)"
fi

if command -v thefuck >/dev/null 2>&1; then
    eval "$(thefuck --alias)"
    [ -n "$wlr_interactive" ] && wlr-good 'thef***'
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

command -v bat >/dev/null 2>&1 && alias cat='bat'
command -v exa >/dev/null 2>&1 && alias ls='exa'

command -v dd-shim >/dev/null 2>&1 && alias dd='dd-shim'
command -v gradle-shim >/dev/null 2>&1 && alias gradle='gradle-shim'

command -v fluxx >/dev/null 2>&1 && alias flux='fluxx'
command -v helmx > /dev/null 2>&1 && alias helm='helmx'
command -v kubectlx > /dev/null 2>&1 && alias kubectl='kubectlx'

command -v sshfsx > /dev/null 2>&1 && alias sshfs='sshfsx'

alias cd='wrappercd'
wrappercd() {
    if [ $# -eq 0 ]; then
        popd >/dev/null 2>&1
    else
        pushd "$1" >/dev/null
    fi
}

mkcd() {
    if [ $# -ne 1 ]; then
        printf 'Usage: mkcd DIRECTORY\n' >&2
        return 1
    fi
    mkdir "$1"
    pushd "$1" >/dev/null
}


unset try_source
unset ensurepath
unset wlr_detect_ssh
unset wlr_suspect_tty
unset wlr_check_env_shim
