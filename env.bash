#!/bin/bash

# different systems and configs seem to make it hard to predict when bashrc and/or bash_profile
# are run, so this script is intended to be sourced by both

# if we've already run, don't run again

if [ -n "$WLR_ENV_BASH" ]; then
    return
fi
WLR_ENV_BASH=y

# output utils, only actually print the warnings/errors if we're in interactive mode

tput_fg_red="$(tput setaf 1)"
tput_fg_yellow="$(tput setaf 3)"
tput_fg_reset="$(tput sgr0)"

wlr_warn() {
    case "$-" in
        *i*) printf '%sWARN:%s %s\n' "$tput_fg_yellow" "$tput_fg_reset" "$1" >&2
    esac
}
wlr_err() {
    case "$-" in
        *i*) printf '%sERR:%s %s\n' "$tput_fg_red" "$tput_fg_reset" "$1" >&2
    esac
}

# other utils

wlr_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
try_source() {
    [ -r "$1" ] && . "$1"
}

# load env vars from .config/env and .config/env_secret

try_source "$wlr_config_dir/env"
try_source "$wlr_config_dir/env_secret"

# remove caps on history size

export HISTSIZE=
export HISTFILESIZE=
export HISTCONTROL=ignoredups

# initialize env shims

wlr_check_env_shim() {
    [ -f "$HOME/.$1/shims/$2" ] || \
        wlr_warn "$1 is installed, but $2 shim is missing ($1 install <version> and/or $1 rehash to fix)"
}

if command -v pyenv >/dev/null 2>&1; then
    eval "$(pyenv init -)"
    if command -v pyenv-virtualenv >/dev/null 2>&1; then
        eval "$(pyenv virtualenv-init -)"
    else
        wlr_warn 'pyenv is installed, but pyenv-virtualenv was not found'
    fi
    wlr_check_env_shim pyenv python
    wlr_check_env_shim pyenv pip
elif command -v python >/dev/null 2>&1; then
    wlr_warn 'python is installed, but pyenv was not found'
fi

if command -v nodenv >/dev/null 2>&1; then
    eval "$(nodenv init -)"
    if command -v nvm >/dev/null 2>&1; then
        wlr_warn 'nodenv is installed, but nvm is also present (you should uninstall nvm)'
    fi
    wlr_check_env_shim nodenv node
    wlr_check_env_shim nodenv npm
    wlr_check_env_shim nodenv npx
elif command -v node >/dev/null 2>&1; then
    wlr_warn 'node is installed, but nodenv was not found'
fi

# initialize PATH for custom aliases, wrappers, and scripts

export PATH="$PATH:$HOME/.local/bin"
export WLR_UNALIASED_PATH="$PATH"

wlr_bin_dir="$HOME/bin"
[ -n "$BASH_SOURCE" ] && wlr_bin_dir="$(realpath "${BASH_SOURCE%/*}")"
while IFS=$'\n' read wlr_bin_subdir; do
    export PATH="$PATH:$wlr_bin_subdir"
done < <(find "$wlr_bin_dir" -mindepth 1 -maxdepth 1 -type d -not -name .git)

# initialize completions and corrections

try_source /usr/share/git/completion/git-completion.bash || \
    try_source /usr/local/etc/profile.d/bash_completion.sh
try_source /usr/share/git/completion/git-prompt.sh
try_source /usr/share/doc/pkgfile/command-not-found.bash

if command -v thefuck >/dev/null 2>&1; then
    eval "$(thefuck --alias)"
fi

# set up prompt

export PS1='\[\033[31m<\]\n\[\033[0G\033[m\]$(foo=$?; [ $foo -gt 0 ] && printf "[%s]" $foo; unset foo)\[\033[0;31m\]\t\[\033[0;32m\]\u@\h\[\033[0;34m\]\w\[\033[m\]\$ '
export PS4='$(foo=$?; [ $foo -gt 0 ] && printf "[%s]" $foo; unset foo)+ ${FUNCNAME[0]:+${FUNCNAME[0]}():}line ${LINENO}: '

if command -v __git_ps1 >/dev/null 2>&1; then
    export PS1='\[\033[31m<\]\n\[\033[0G\033[m\]$(foo=$?; [ $foo -gt 0 ] && printf "[%s]" $foo; unset foo)\[\033[0;31m\]\t\[\033[0;32m\]\u@\h\[\033[0;34m\]\w\[\033[0;33m\]$(__git_ps1 "(%s)")\[\033[m\]\$ '
fi

# use aliases to resolve naming conflicts and overwrite default behaviour

alias gs='git status'

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
