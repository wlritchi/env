#!/usr/bin/env xonsh

from importlib import import_module
import os
import sys

import xonsh
from xonsh.ansi_colors import register_custom_ansi_style

$XONSH_HISTORY_BACKEND = 'sqlite'
$XONSH_HISTORY_SIZE = '1000000 commands'

all_missing_packages = set()


def can_autoinstall():
    return '.local/pipx/venvs/xonsh' in $(which xpip)


def autoinstall(pkgname):
    wlr-working 'xonsh - installing' @(pkgname)
    return ![xpip install @(pkgname)]


def ensure_packages(*packages):
    missing_packages = set()
    for pkg in packages:
        pkgname = pkg
        if isinstance(pkg, list):
            [pkg, pkgname] = pkg
        try:
            import_module(pkg)
        except:
            if can_autoinstall():
                if autoinstall(pkgname):
                    continue
            missing_packages.add(pkgname)
            all_missing_packages.add(pkgname)
    if missing_packages:
        return False
    return True


if ensure_packages(['xontrib.argcomplete', 'xontrib-argcomplete']):
    xontrib load argcomplete
if ensure_packages(['xontrib.pipeliner', 'xontrib-pipeliner']):
    xontrib load pipeliner
if ![which tcg >/dev/null 2>&1]:
    if ensure_packages(['xontrib.tcg', 'xonsh-tcg']):
        xontrib load tcg
xontrib load vox # shipped with xonsh
if ensure_packages(['prompt_toolkit', 'prompt-toolkit']):
    xontrib load whole_word_jumping
if ![which zoxide >/dev/null 2>&1]:
    if ensure_packages(['xontrib.zoxide', 'xontrib-zoxide']):
        xontrib load zoxide


ensure_packages('pygments', 'pytimeparse')


from xonsh.pyghooks import pygments_version_info, register_custom_pygments_style


def _wrap_source(source_fn):
    """Wrap the source alias to handle attempts to activate a venv.

    Some tools, such as VS Code, run a shell and type
        source <path>/bin/activate
    into that shell, in order for the shell to run in the venv.
    Unfortunately, xonsh does not play well with standard venv activation
    scripts. Instead, xonsh provides the vox xontrib, loaded above, which
    offers similar functionality. This wrapper catches attepts to source venv
    activation scripts (which wouldn't work anyway, as xonsh's source expects
    only xonsh-flavoured inputs), and converts them into calls to vox."""

    def wrapper(args):
        if len(args) == 1 and args[0].endswith('/bin/activate'):
            virtualenv_name = args[0][:-13]
            vox activate @(virtualenv_name)
        else:
            source_fn(args)

    return wrapper


aliases['source'] = _wrap_source(aliases['source'])


def setup_colors():
    if not ensure_packages(
        'pygments',
        ['prompt_toolkit', 'prompt-toolkit'],
    ):
        return
    solarized = {
        'BASE03' : '#002b36',
        'BASE02' : '#073642',
        'BASE01' : '#586e75',
        'BASE00' : '#657b83',
        'BASE0'  : '#839496',
        'BASE1'  : '#93a1a1',
        'BASE2'  : '#eee8d5',
        'BASE3'  : '#fdf6e3',
        'RED'    : '#dc322f',
        'ORANGE' : '#cb4b16',
        'YELLOW' : '#b58900',
        'GREEN'  : '#859900',
        'CYAN'   : '#2aa198',
        'BLUE'   : '#268bd2',
        'VIOLET' : '#6c71c4',
        'MAGENTA': '#d33682',
    }

    style = {}

    for color in ['RED', 'YELLOW', 'GREEN', 'CYAN', 'BLUE']:
        style[color] = solarized[color]
        style[f'INTENSE_{color}'] = solarized[color]
    style['PURPLE'] = solarized['VIOLET']
    style['INTENSE_PURPLE'] = solarized['VIOLET']
    style['WHITE'] = solarized['BASE0']
    style['INTENSE_WHITE'] = solarized['BASE1']
    style['BLACK'] = solarized['BASE03']
    style['INTENSE_BLACK'] = solarized['BASE02']

    style['RESET'] = style['WHITE'] # would like to have 'noinherit ' on the front but xonsh bug

    # style['Token'] = style['WHITE'] # xonsh bug
    style['Token.Keyword'] = solarized['YELLOW']
    style['Token.Literal.String'] = solarized['GREEN']
    style['Token.Literal.Number'] = solarized['MAGENTA']
    style['Token.Comment'] = solarized['BASE01']
    style['Token.Comment.Special'] = solarized['CYAN']

    # would use xonsh.tools helper to register both of these, but solarized-dark is only available in pygments
    # need to use monokai as base for ansi styles
    register_custom_ansi_style('solarized-dark-term', style, 'monokai')
    if pygments_version_info():
        register_custom_pygments_style('solarized-dark-term', style, None, None, 'solarized-dark')

    # still don't like this way of passing config, but here we are
    $XONSH_COLOR_STYLE = 'solarized-dark-term'


setup_colors()


# set up prompt
def _prompt():
    global _
    rtn_str = ''
    try:
        if _.rtn != 0:
            rtn_str = f'[{_.rtn}]'
    except AttributeError: # previous command has no return code (e.g. because it's a xonsh function)
        pass
    except NameError: # no _, no previous command
        pass
    rtn_formatted = '{RED}<\n{RESET}' + rtn_str
    return rtn_formatted + '{YELLOW}{localtime}{GREEN}{user}@{hostname}{BLUE}{cwd}{YELLOW}{curr_branch:({})}{RESET}$ '
$PROMPT = _prompt


# use aliases to resolve naming conflicts and overwrite default behaviour

aliases['gap'] = 'git add -p' # some algebra package
aliases['gm'] = 'git merge' # graphicsmagick
aliases['gs'] = 'git status' # ghostscript

if $(which bat 2>/dev/null):
    aliases['cat'] = 'bat'
if $(which exa 2>/dev/null):
    aliases['ls'] = 'exa'

if $(which dd-shim 2>/dev/null):
    aliases['dd'] = 'dd-shim'
if $(which gradle-shim 2>/dev/null):
    aliases['gradle'] = 'gradle-shim'

if $(which fluxx 2>/dev/null):
    aliases['flux'] = 'fluxx'
if $(which helmx 2>/dev/null):
    aliases['helm'] = 'helmx'
if $(which fluxx 2>/dev/null):
    aliases['kubectl'] = 'kubectlx'

if $(which sshx 2>/dev/null):
    aliases['ssh'] = 'sshx'
if $(which sshfsx 2>/dev/null):
    aliases['sshfs'] = 'sshfsx'
if $(which moshx 2>/dev/null):
    aliases['mosh'] = 'moshx'

# xonsh-only, workaround for lack of ergonomic "time" builtin
if $(which timex 2>/dev/null):
    aliases['time'] = 'timex'

def _cd(args):
    if len(args) > 0:
        _r = xonsh.dirstack.pushd(args)
        if _r[1] is not None:
            print(_r[1].strip(), file=sys.stderr)
        return _r[2]
    else:
        xonsh.dirstack.popd(args)
aliases['cd'] = _cd

def _mkcd(args):
    if len(args) != 1:
        print('Usage: mkcd DIRECTORY', file=sys.stderr)
        return 1
    dir = args[0]
    os.mkdir(dir)
    xonsh.dirstack.pushd([dir])
aliases['mkcd'] = _mkcd

# wrapper for bash that sets WLR_XONSH=n to avoid re-executing xonsh from .bashrc
# implemented as an alias in case we want to switch back to bash for some reason
def _bash(args):
    bash_bin = ${...}.get('WLR_BASH_BIN', '/usr/bin/bash')
    with ${...}.swap(WLR_XONSH='n'):
        @(bash_bin) @(args)
aliases['bash'] = _bash

# temporary workaround for xonsh bug in 0.9.27
# see https://github.com/xonsh/xonsh/issues/4243 and https://github.com/xonsh/xonsh/issues/2404
aliases['gs'] = '$[git status]'
def _gd(args):
    $[git diff @(args)]
aliases['gd'] = _gd
def _glog(args):
    $[~/.wlrenv/bin/aliases/glog @(args)]
aliases['glog'] = _glog
def _gtree(args):
    $[~/.wlrenv/bin/aliases/gtree @(args)]
aliases['gtree'] = _gtree


# last step: warn about missing packages
if all_missing_packages:
    wlr-warn 'xonsh - missing packages for standard environment (xpip install' @(all_missing_packages) 'to fix)'
