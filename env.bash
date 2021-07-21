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


wlr_interactive=
case "$-" in
    *i*)
        wlr_interactive=y
        ;;
esac

wlr_good() {
    if [ -n "$wlr_interactive" ]; then
        tput setaf 2
        printf '✔ '
        tput sgr0
        echo "$@"
    fi
}

wlr_working() {
    if [ -n "$wlr_interactive" ]; then
        tput setaf 4
        printf '↻ '
        tput sgr0
        echo "$@"
    fi
}

wlr_warn() {
    if [ -n "$wlr_interactive" ]; then
        tput setaf 3
        printf '⚠ '
        tput sgr0
        echo "$@"
    fi
}

wlr_err() {
    if [ -n "$wlr_interactive" ]; then
        tput setaf 1
        printf '❌ '
        tput sgr0
        echo "$@"
    fi
}

wlr_countdown() {
    if [ -n "$wlr_interactive" ]; then
        wlr_cancelled=
        trap 'wlr_cancelled=y' INT
        tput civis
        tput sc
        for i in 2 1; do
            for char in ⠋ ⠙ ⠸ ⠴ ⠦ ⠇; do
                tput rc
                printf '%s %s %s' "$char" "${1:-}" "$i"
                if ! sleep 0.1; then
                    wlr_cancelled=y
                    break 2
                fi
            done
        done
        tput rc
        tput el
        tput cnorm
        trap - INT
        [ -z "$wlr_cancelled" ]
    fi
}

try_source() {
    if [ -r "$1" ]; then
        . "$1"
    else
        return 1
    fi
}


# early environment setup

if [ -z "$WLR_ENV_BASH" ]; then
    export WLR_ENV_PATH="$HOME/.wlrenv"
    [ -n "$BASH_SOURCE" ] && export WLR_ENV_PATH="$(realpath "${BASH_SOURCE%/*}")"

    # remove caps on history size

    export HISTSIZE=
    export HISTFILESIZE=
    export HISTCONTROL=ignoredups

    # on macOS, use SSH_AUTH_SOCK_LOCAL over SSH_AUTH_SOCK if present

    if [ "$(uname)" == Darwin ] && [ -n "$SSH_AUTH_SOCK_LOCAL" ]; then
        export SSH_AUTH_SOCK="$SSH_AUTH_SOCK_LOCAL"
    fi

    # load env vars from .config/env and .config/env_secret

    try_source "${XDG_CONFIG_HOME:-$HOME/.config}/env"
    try_source "${XDG_CONFIG_HOME:-$HOME/.config}/env_secret"
fi


# check for update, at most once an hour

if ! [ -e "$WLR_ENV_PATH/meta/.last-update-check" ] || [ -n "$(find "$WLR_ENV_PATH/meta/.last-update-check" -mmin +60 -print -quit)" ]; then
    wlr_working env update check
    if "$WLR_ENV_PATH/meta/git-verify-fast-forward"; then
        touch "$WLR_ENV_PATH/meta/.last-update-check"
        . "$WLR_ENV_PATH/env.bash"
        return
    else
        wlr_err env update failed
    fi
else
    wlr_good env up to date
fi


# invoke tmux, if applicable

if [ -n "$wlr_interactive" ]; then
    if [ -n "$TMUX" ]; then
        wlr_good 'tmux'
    elif [ "$WLR_TMUX" == 'n' ]; then
        wlr_warn 'tmux - skipping'
    elif command -v tmux >/dev/null 2>&1; then
        if [ -n "$DISPLAY" ] || wlr_countdown tmux; then
            exec tmux new
        fi
    else
        wlr_err 'tmux'
    fi
fi


# initialize env shims

wlr_check_env_shim() {
    if ! [ -f "$HOME/.$1/shims/$2" ]; then
        wlr_warn "$1 is installed, but $2 shim is missing ($1 install <version> and/or $1 rehash to fix)"
        return 1
    fi
}

if command -v pyenv >/dev/null 2>&1; then
    if ! pyenv --version | grep -q 'pyenv 1'; then
        # pyenv 2+ splits init into two parts
        # "pyenv init --path" updates PATH (intended for .bash_profile)
        # "pyenv init -" sets functions, completions, etc (also curiously PYENV_SHELL env var still lives here)
        [ -z "$WLR_ENV_BASH" ] && eval "$(pyenv init --path)"
    fi
    eval "$(pyenv init -)"
    if command -v pyenv-virtualenv >/dev/null 2>&1; then
        eval "$(pyenv virtualenv-init -)"
    else
        wlr_warn 'pyenv is installed, but pyenv-virtualenv was not found'
    fi
    wlr_check_env_shim pyenv python && \
        wlr_check_env_shim pyenv pip && \
        wlr_good 'pyenv'
elif command -v python >/dev/null 2>&1; then
    wlr_warn 'python is installed, but pyenv was not found'
else
    wlr_err 'python'
fi

if command -v nodenv >/dev/null 2>&1; then
    eval "$(nodenv init -)"
    if command -v nvm >/dev/null 2>&1; then
        wlr_warn 'nodenv is installed, but nvm is also present (you should uninstall nvm)'
    fi
    if ! command -v node-build >/dev/null 2>&1; then
        wlr_warn 'nodenv is installed, but node-build is missing (install nodenv-node-build-git to fix)'
    fi
    wlr_check_env_shim nodenv node && \
        wlr_check_env_shim nodenv npm && \
        wlr_check_env_shim nodenv npx && \
        wlr_good 'nodenv'
elif command -v node >/dev/null 2>&1; then
    wlr_warn 'node is installed, but nodenv was not found'
else
    wlr_err 'node'
fi

if command -v pipx >/dev/null 2>&1; then
    wlr_good 'pipx'
else
    wlr_err 'pipx'
fi

if command -v conda >/dev/null 2>&1; then
    if [ -d "$HOME/.conda/envs/main" ]; then
        if eval "$(conda shell.posix activate main)"; then
            wlr_good 'conda'
        else
            wlr_err 'conda'
        fi
    else
        wlr_warn 'conda is installed, but main env is missing (conda create -n main to fix)'
    fi
else
    wlr_err 'conda'
fi


# initialize PATH for custom aliases, wrappers, and scripts

if [ -z "$WLR_UNALIASED_PATH" ]; then
    export PATH="$PATH:$HOME/.local/bin:$HOME/.cargo/bin"
    export WLR_UNALIASED_PATH="$PATH"

    while IFS=$'\n' read wlr_env_subdir; do
        export PATH="$PATH:$wlr_env_subdir"
    done < <(find "$WLR_ENV_PATH/bin" -mindepth 1 -maxdepth 1 -type d -not -name .git)
    unset wlr_env_subdir
fi


# prevent re-executing this setup

export WLR_ENV_BASH=y


# invoke xonsh, if applicable

if [ -n "$wlr_interactive" ]; then
    if [ "$WLR_XONSH" == 'y' ]; then
        wlr_err 'xonsh failed reentrancy check'
    elif [ "$WLR_XONSH" == 'n' ]; then
        wlr_warn 'xonsh - skipping'
    elif command -v xonsh >/dev/null 2>&1; then
        if [ -n "$DISPLAY" ] || wlr_countdown 'xonsh'; then
            wlr_working 'xonsh'
            export WLR_XONSH=y
            exec xonsh
        fi
    elif command -v pipx >/dev/null 2>&1; then
        wlr_err 'xonsh is not installed (but you can install it with `pipx install xonsh`'
    else
        wlr_err 'xonsh'
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
    wlr_good 'thef***'
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

command -v exa >/dev/null 2>&1 && alias ls='exa'

command -v gradle-shim >/dev/null 2>&1 && alias gradle='gradle-shim'

alias cd='wrappercd'
wrappercd() {
    if [ $# -eq 0 ]; then
        popd >/dev/null 2>&1
    else
        pushd "$1" >/dev/null
    fi
}


unset wlr_good
unset wlr_working
unset wlr_warn
unset wlr_err
unset wlr_countdown
unset try_source
unset wlr_check_env_shim
