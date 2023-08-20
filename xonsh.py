#!/usr/bin/env python3

import base64
import json
import os
import random
import subprocess
import sys
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import product
from os.path import basename, dirname, exists, lexists, isabs, isfile, isdir, islink, ismount, realpath, relpath, samefile
from random import randint
from typing import Any, Callable, Dict, List, Optional, ParamSpec, Tuple, TypeVar

import xonsh
from xonsh.ansi_colors import register_custom_ansi_style
from xonsh.built_ins import XSH
from xonsh.xontribs import xontribs_load
from xonsh.xoreutils import _which

try:
    import numpy as np
    from numpy.typing import NDArray
except Exception:
    pass

XSH.env['XONSH_HISTORY_BACKEND'] = 'sqlite'
XSH.env['XONSH_HISTORY_SIZE'] = '1000000 commands'
XSH.env['fzf_history_binding'] = 'c-r'


def _setup():
    def which(bin):
        try:
            _which.which(bin)
            return True
        except _which.WhichError:
            return False


    def can_autoinstall():
        return '.local/pipx/venvs/xonsh' in sys.prefix


    def autoinstall(pkgname):
        print(f"↻ xonsh - installing {pkgname}")  # TODO color ↻ blue
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', pkgname], check=True)
            return True
        except subprocess.CalledProcessError:
            print(f"🗙 xonsh - failed to install {pkgname}")  # TODO color 🗙 red
            return False


    def ensure_packages(missing_package_collector, *packages):
        # lazy import
        from importlib import import_module
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
        if missing_packages:
            missing_package_collector |= missing_packages
        return not missing_packages


    CONVENIENCE_PACKAGES = (
        'numpy',  # imported as np if available
        'pytimeparse',  # used by randtimedelta
        'skimage',
    )

    def prepare_packages():
        xontribs = [
            'xontrib.argcomplete',
            'xontrib_avox_poetry',
            'xontrib.jedi',
            'xontrib.pipeliner',
            'xontrib.vox',
            ['xontrib.whole_word_jumping', 'prompt_toolkit'],
        ]
        if which('fzf'):
            xontribs.append('xontrib.fzf-widgets')
        if which('zoxide'):
            xontribs.append('xontrib.zoxide')

        missing_packages = set()
        for xontrib in xontribs:
            if not isinstance(xontrib, list):
                xontrib = [xontrib]
            xontrib_packages = [
                [name, name.replace('_', '-').replace('.', '-')]
                for name in xontrib
            ]
            if ensure_packages(missing_packages, *xontrib_packages):
                xontribs_load([xontrib[0][8:]])
        ensure_packages(missing_packages, *CONVENIENCE_PACKAGES)

        if missing_packages:
            # TODO color ⚠ yellow
            print(f"⚠ xonsh - missing packages for standard environment (xpip install {' '.join(missing_packages)} to fix)")


    def setup_colors():
        if not ensure_packages(
            set(),
            'pygments',
            ['prompt_toolkit', 'prompt-toolkit'],
        ):
            return

        from xonsh.pyghooks import pygments_version_info, register_custom_pygments_style

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
        XSH.env['XONSH_COLOR_STYLE'] = 'solarized-dark-term'


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


    XSH.env['PROMPT'] = _prompt


    def prepare_aliases():
        # use aliases to resolve naming conflicts and overwrite default behaviour

        XSH.aliases['gap'] = 'git add -p'  # some algebra package
        XSH.aliases['gm'] = 'git merge'  # graphicsmagick
        XSH.aliases['gs'] = 'git status'  # ghostscript

        if which('grmx'):  # macos, with brew: gnu rm
            XSH.aliases['grm'] = 'grmx'

        if which('bat'):
            XSH.aliases['cat'] = 'bat'
        if which('exa'):
            XSH.aliases['ls'] = 'exa'

        if which('dd-shim'):
            XSH.aliases['dd'] = 'dd-shim'
        if which('gradle-shim'):
            XSH.aliases['gradle'] = 'gradle-shim'

        if which('fluxx'):
            XSH.aliases['flux'] = 'fluxx'
        if which('helmx'):
            XSH.aliases['helm'] = 'helmx'
        if which('fluxx'):
            XSH.aliases['kubectl'] = 'kubectlx'

        if which('sshx'):
            XSH.aliases['ssh'] = 'sshx'
        if which('sshfsx'):
            XSH.aliases['sshfs'] = 'sshfsx'
        if which('moshx'):
            XSH.aliases['mosh'] = 'moshx'

        # xonsh-only, workaround for lack of ergonomic "time" builtin
        if which('timex'):
            XSH.aliases['time'] = 'timex'

        def _cd(args):
            if len(args) > 0:
                _r = xonsh.dirstack.pushd(args)
                if _r[1] is not None:
                    print(_r[1].strip(), file=sys.stderr)
                return _r[2]
            else:
                xonsh.dirstack.popd(args)
        XSH.aliases['cd'] = _cd

        def _mkcd(args):
            if len(args) != 1:
                print('Usage: mkcd DIRECTORY', file=sys.stderr)
                return 1
            dir = args[0]
            os.mkdir(dir)
            xonsh.dirstack.pushd([dir])
        XSH.aliases['mkcd'] = _mkcd

        # # temporary workaround for xonsh bug in 0.9.27
        # # see https://github.com/xonsh/xonsh/issues/4243 and https://github.com/xonsh/xonsh/issues/2404
        # XSH.aliases['gs'] = '$[git status]'
        # def _gd(args):
        #     $[git diff @(args)]
        # XSH.aliases['gd'] = _gd
        # def _glog(args):
        #     $[~/.wlrenv/bin/aliases/glog @(args)]
        # XSH.aliases['glog'] = _glog
        # def _gtree(args):
        #     $[~/.wlrenv/bin/aliases/gtree @(args)]
        # XSH.aliases['gtree'] = _gtree

        def _source(source_fn):
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
                    from xontrib.voxapi import Vox
                    Vox().activate(virtualenv_name)
                else:
                    source_fn(args)

            return wrapper
        XSH.aliases['source'] = _source(XSH.aliases['source'])


    def late_init():
        prepare_aliases()
        prepare_packages()


    threading.Thread(target=late_init).start()


_setup()
del _setup


def coin():
    return 'heads' if randint(0, 1) else 'tails'


def ndm(n=1, m=6):
    return sum(randint(1, m) for _ in range(n))


def d4(n=1):
    return ndm(n, 4)


def d6(n=1):
    return ndm(n, 6)


def d8(n=1):
    return ndm(n, 8)


def d20(n=1):
    return ndm(n, 20)


def shuffle(items):
    l = list(items)
    random.shuffle(l)
    return l


def choose(items):
    l = list(items)
    return l[randint(0, len(l) - 1)]


def parsetimedelta(x):
    if isinstance(x, str):
        from pytimeparse.timeparse import timeparse
        x = timeparse(x)
    if isinstance(x, int) or isinstance(x, float):
        x = timedelta(seconds=x)
    if not isinstance(x, timedelta):
        raise ValueError(f"Expected string, number of seconds, or timedelta instance; got {timedelta}")
    return x


def randtimedelta(a, b=None):
    if b is None:
        a, b = (timedelta(0), a)
    a = parsetimedelta(a)
    b = parsetimedelta(b)
    seconds = randint(int(a.total_seconds()), int(b.total_seconds()))
    return str(timedelta(seconds=seconds))


def snap_to_grid(point, grid_spacing=10, grid_reference=0):
    return grid_reference + grid_spacing * round((point - grid_reference) / grid_spacing)


def bits(n):
    # https://stackoverflow.com/a/4859937
    if isinstance(n, str):
        n = int(n, 16)
    return bin(n)[2:].zfill(8)


def lines(file):
    with open(file, 'r') as f:
        return [x.strip() for x in f.readlines()]
