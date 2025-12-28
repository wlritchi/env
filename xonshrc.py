#!/usr/bin/env python3

# ruff: noqa: F401, T201

import base64
import json
import os
import random
import shlex
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from itertools import product
from math import log, log2, sqrt
from os.path import (
    basename,
    dirname,
    exists,
    isabs,
    isdir,
    isfile,
    islink,
    ismount,
    lexists,
    realpath,
    samefile,
)
from pathlib import Path
from pprint import pprint
from random import randint
from typing import (
    Any,
    ParamSpec,
    TypeVar,
)

import numpy as np
import tiktoken
import xonsh
import xonsh.dirstack
from catppuccin import PALETTE
from catppuccin.extras.pygments import MacchiatoStyle
from numpy.typing import NDArray
from openai import OpenAI
from pygments.token import Token
from pytimeparse.timeparse import timeparse
from xonsh.built_ins import XSH
from xonsh.pyghooks import register_custom_pygments_style
from xonsh.xontribs import xontribs_load
from xonsh.xoreutils import _which
from xontrib.voxapi import Vox

if not XSH.env:
    raise RuntimeError('xonsh is not loaded')

XSH.env['XONSH_SHOW_TRACEBACK'] = True
XSH.env['XONSH_HISTORY_BACKEND'] = 'sqlite'
XSH.env['XONSH_HISTORY_SIZE'] = '1000000 commands'
XSH.env['fzf_history_binding'] = 'c-r'


def _setup() -> None:
    if not XSH.aliases or not XSH.env:
        raise RuntimeError('xonsh is not loaded')

    def which(bin: str) -> bool:
        try:
            _which.which(bin)
            return True
        except _which.WhichError:
            return False

    def prepare_packages() -> None:
        for xontrib in (
            'argcomplete',
            'autovox',
            'avox_poetry',
            'jedi',
            'pipeliner',
            'vox',
            'whole_word_jumping',
            'fzf-widgets',
            'zoxide',
        ):
            stdout, stderr, _ecode = xontribs_load([xontrib])
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)

    def setup_colors() -> None:
        if not XSH.env:
            raise RuntimeError('xonsh is not loaded')

        catppuccin_macchiato = PALETTE.macchiato.colors
        color_tokens = {
            getattr(Token.Color, color.name.upper()): color.hex
            for color in catppuccin_macchiato
        }
        intense_color_tokens = {
            getattr(Token.Color, f'INTENSE_{color.name.upper()}'): color.hex
            for color in catppuccin_macchiato
        }
        color_map = {
            **MacchiatoStyle.styles,
            **color_tokens,
            **intense_color_tokens,
            # alias other color names xonsh expects
            Token.Color.PURPLE: catppuccin_macchiato.pink.hex,
            Token.Color.INTENSE_PURPLE: catppuccin_macchiato.pink.hex,
            Token.Color.CYAN: catppuccin_macchiato.teal.hex,
            Token.Color.INTENSE_CYAN: catppuccin_macchiato.teal.hex,
            Token.Color.WHITE: catppuccin_macchiato.subtext0.hex,
            Token.Color.INTENSE_WHITE: catppuccin_macchiato.subtext1.hex,
            Token.Color.BLACK: catppuccin_macchiato.surface1.hex,
            Token.Color.INTENSE_BLACK: catppuccin_macchiato.surface2.hex,
        }
        register_custom_pygments_style(
            'catppuccin-macchiato-term',
            color_map,
            # base='catppuccin-macchiato',
        )
        XSH.env['XONSH_COLOR_STYLE'] = 'catppuccin-macchiato-term'

    setup_colors()

    gpt_model_pricing = {  # prompt, completion, per million tokens
        'gpt-4o': (2.5, 10),
        'gpt-4o-mini': (0.15, 0.6),
        'gpt-4.1': (2, 8),
        'gpt-4.1-mini': (0.4, 1.6),
        'gpt-4.1-nano': (0.1, 0.4),
        'gpt-4.5-preview': (75, 150),
        'o1': (15, 60),
        'o1-mini': (1.1, 4.4),
        'o1-pro': (150, 600),
        'o3': (2, 8),
        'o3-mini': (1.1, 4.4),
        'o3-deep-research': (10, 40),
        'o4-mini': (1.1, 4.4),
        'o4-mini-deep-research': (2, 8),
    }

    gpt_cost_acc = 0
    gpt_messages = []
    gpt_tokens = 0

    def _query_gpt(query: list[str], model: str) -> None:
        nonlocal gpt_cost_acc, gpt_messages, gpt_tokens

        encoder = None
        try:
            encoder = tiktoken.encoding_for_model(model.replace('o4', 'o3'))
        except KeyError:
            pass

        # cheapo bare words approximation
        if len(query) > 1:
            query_str = ' '.join(f'"{q}"' if ' ' in q else q for q in query)
        else:
            query_str = query[0]

        prompt_tokens = 0
        if encoder is not None:
            tokens_per_message = 3
            tokens_per_role_switch = 1
            extra_tokens = tokens_per_message * 2 + tokens_per_role_switch
            if gpt_messages:
                extra_tokens += tokens_per_role_switch
            prompt_tokens = len(encoder.encode(query_str)) + extra_tokens

        print(f'[{model}]')

        gpt_messages.append(
            {
                'role': 'user',
                'content': query_str,
            }
        )

        client = OpenAI()
        response = client.chat.completions.create(
            model=model,
            messages=gpt_messages,
            stream=True,
        )

        response_message = {}
        for chunk in response:
            chunk_delta = chunk.choices[0].delta
            if chunk_delta.role:
                response_message['role'] = chunk_delta.role
            if chunk_delta.content:
                print(chunk_delta.content, end='')
                response_message['content'] = (
                    response_message.get('content', '') + chunk_delta.content
                )
        # print(chunk)
        print()
        gpt_messages.append(response_message)

        completion_tokens = (
            len(encoder.encode(response_message['content']))
            if encoder is not None
            else 0
        )
        gpt_tokens += prompt_tokens + completion_tokens

        prompt_price, completion_price = gpt_model_pricing[model]
        gpt_cost_acc += (
            prompt_price * prompt_tokens + completion_price * completion_tokens
        ) / 1000000

    def _gpt(query: list[str]) -> None:
        _query_gpt(query, 'o4-mini')

    XSH.aliases['gpt'] = _gpt

    # set up prompt
    def _prompt() -> str:
        global _  # ty: ignore[unresolved-global]  # xonsh magic variable
        nonlocal gpt_cost_acc, gpt_tokens

        rtn_str = ''
        try:
            if _.rtn != 0:  # type: ignore[name-defined]  # xonsh magic variable
                rtn_str = f'{_.rtn}'  # type: ignore[name-defined]
        except AttributeError:
            # previous command has no return code (e.g. because it's a xonsh function)
            pass
        except NameError:
            # no _, no previous command
            pass

        if rtn_str:
            rtn_str = f'{{RED}}[{rtn_str}]'

        gpt_cost_str = (
            f'{{BLUE}}{gpt_cost_acc:.2f}|{gpt_tokens})' if gpt_cost_acc else ''
        )
        rtn_formatted = '\n' + gpt_cost_str + rtn_str
        return (
            rtn_formatted
            + '{YELLOW}{localtime}{GREEN}{user}@{hostname}{BLUE}{cwd}{YELLOW}{curr_branch:({})}{RESET}$ '
        )

    XSH.env['PROMPT'] = _prompt

    def prepare_aliases() -> None:
        """Use aliases to resolve naming conflicts and overwrite default behaviour."""

        if not XSH.aliases or not xonsh.dirstack:
            raise RuntimeError('xonsh is not loaded')

        XSH.aliases['gap'] = 'git add -p'  # some algebra package
        XSH.aliases['gm'] = 'git merge'  # graphicsmagick
        XSH.aliases['gs'] = 'git status'  # ghostscript

        if which('grmx'):  # macos, with brew: gnu rm
            XSH.aliases['grm'] = 'grmx'

        if which('bat'):
            XSH.aliases['cat'] = 'bat'
        if which('lsx') and which('eza'):
            XSH.aliases['ls'] = 'lsx'
        elif which('eza'):
            XSH.aliases['ls'] = 'eza'
        elif which('exa'):
            XSH.aliases['ls'] = 'exa'
        if which('nvim'):
            XSH.aliases['vim'] = 'nvim'

        if which('dd-shim'):
            XSH.aliases['dd'] = 'dd-shim'
        if which('gradle-shim'):
            XSH.aliases['gradle'] = 'gradle-shim'
        if which('rsync-shim'):
            XSH.aliases['rsync'] = 'rsync-shim'
        if which('yay-shim'):
            XSH.aliases['yay'] = 'yay-shim'

        if which('k9sx'):
            XSH.aliases['k9s'] = 'k9sx'

        if which('sshx'):
            XSH.aliases['ssh'] = 'sshx'
        if which('sshfsx'):
            XSH.aliases['sshfs'] = 'sshfsx'
        if which('moshx'):
            XSH.aliases['mosh'] = 'moshx'

        # xonsh-only, workaround for lack of ergonomic "time" builtin
        if which('timex'):
            XSH.aliases['time'] = 'timex'

        _has_zoxide = bool(which('zoxide'))
        _has_fnm = bool(which('fnm'))

        def _fnm_use() -> None:
            if not XSH.env:
                raise RuntimeError('xonsh is not loaded')

            if _has_fnm and 'FNM_MULTISHELL_PATH' in XSH.env:
                subprocess.run(['fnm', 'use', '--silent-if-unchanged'])  # noqa: S603, S607

        def _cd(args: list[str]) -> None:
            if not XSH.env:
                raise RuntimeError('xonsh is not loaded')

            match args:
                case [] | ['-']:
                    xonsh.dirstack.popd([])
                case [dir_name, *_rest]:
                    if os.path.isdir(dir_name):
                        xonsh.dirstack.pushd([dir_name])
                    elif _has_zoxide:
                        try:
                            cmd = subprocess.run(  # noqa: S603  # pyright: ignore[reportCallIssue]
                                [  # noqa: S607
                                    'zoxide',
                                    'query',
                                    '--exclude',
                                    XSH.env.get('PWD'),  # pyright: ignore[reportArgumentType]
                                    '--',
                                    *args,
                                ],
                                check=True,
                                capture_output=True,
                                encoding='utf-8',
                            )
                            xonsh.dirstack.pushd([cmd.stdout[:-1]])
                        except subprocess.CalledProcessError:
                            print(
                                f"No directories matched query '{args}'",
                                file=sys.stderr,
                            )
                    else:
                        print(
                            f"Directory {dir_name} does not exist, and zoxide is not available"
                        )
            _fnm_use()

        XSH.aliases['cd'] = _cd

        def _mkcd(args: list[str]) -> int | None:
            if len(args) != 1:
                print('Usage: mkcd DIRECTORY', file=sys.stderr)
                return 1
            dir_name = args[0]
            os.mkdir(dir_name)
            _cd([dir_name])

        XSH.aliases['mkcd'] = _mkcd

        def _try(args: list[str]) -> None:
            """Wrapper for try - fresh directories for every vibe."""
            if not XSH.env:
                raise RuntimeError('xonsh is not loaded')

            try_path = os.path.expanduser('~/src/tries')
            try:
                with (
                    open('/dev/tty') as tty_in,
                    open('/dev/tty', 'w') as tty_out,
                ):
                    result = subprocess.run(  # noqa: S603
                        ['try', 'exec', '--path', try_path, *args],  # noqa: S607
                        stdin=tty_in,
                        stdout=subprocess.PIPE,
                        stderr=tty_out,
                        text=True,
                    )
                if result.returncode == 0 and result.stdout.strip():
                    # Join line continuations (backslash-newline)
                    script = result.stdout.replace('\\\n', '')
                    for line in script.strip().split('\n'):
                        # Skip comments
                        if line.strip().startswith('#'):
                            continue
                        for part in line.split('&&'):
                            try:
                                tokens = shlex.split(part)
                            except ValueError:
                                print(f'try: shlex parse error for: {part!r}')
                                continue
                            if not tokens:
                                continue
                            elif tokens[0] == 'cd' and len(tokens) > 1:
                                _cd([tokens[1]])
                            elif tokens[0] == 'touch' and len(tokens) > 1:
                                Path(tokens[1]).touch()
                            elif (
                                len(tokens) >= 3
                                and tokens[0] == 'mkdir'
                                and tokens[1] == '-p'
                            ):
                                os.makedirs(tokens[2], exist_ok=True)
                            else:
                                print(f'try: unhandled command: {tokens}')
                elif result.stdout:
                    print(result.stdout)
            except FileNotFoundError:
                print('try command not found', file=sys.stderr)

        if which('try'):
            XSH.aliases['try'] = _try

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

        def _source(source_fn: Callable) -> Callable:
            """Wrap the source alias to handle attempts to activate a venv.

            Some tools, such as VS Code, run a shell and type
                source <path>/bin/activate
            into that shell, in order for the shell to run in the venv.
            Unfortunately, xonsh does not play well with standard venv activation
            scripts. Instead, xonsh provides the vox xontrib, loaded above, which
            offers similar functionality. This wrapper catches attempts to source venv
            activation scripts (which wouldn't work anyway, as xonsh's source expects
            only xonsh-flavoured inputs), and converts them into calls to vox."""

            def wrapper(args: list[str]) -> None:
                if len(args) == 1 and args[0].endswith('/bin/activate'):
                    virtualenv_name = args[0][:-13]
                    Vox().activate(virtualenv_name)
                else:
                    source_fn(args)

            return wrapper

        XSH.aliases['source'] = _source(XSH.aliases['source'])

    prepare_aliases()
    prepare_packages()


_setup()
del _setup


def coin() -> str:
    return 'heads' if randint(0, 1) else 'tails'  # noqa: S311


def ndm(n: int = 1, m: int = 6) -> int:
    return sum(randint(1, m) for _ in range(n))  # noqa: S311


def d4(n: int = 1) -> int:
    return ndm(n, 4)


def d6(n: int = 1) -> int:
    return ndm(n, 6)


def d8(n: int = 1) -> int:
    return ndm(n, 8)


def d20(n: int = 1) -> int:
    return ndm(n, 20)


def shuffle[T](items: list[T]) -> list[T]:
    new_list = list(items)
    random.shuffle(new_list)
    return new_list


def choose[T](items: list[T]) -> T:
    new_list = list(items)
    return new_list[randint(0, len(new_list) - 1)]  # noqa: S311


def parsetimedelta(x: str | int | float | timedelta) -> timedelta:
    if isinstance(x, str):
        seconds = timeparse(x)
        if seconds is not None:
            return timedelta(seconds=seconds)
    if isinstance(x, int) or isinstance(x, float):
        return timedelta(seconds=x)
    if not isinstance(x, timedelta):
        raise ValueError(
            f"Expected string, number of seconds, or timedelta instance; got {timedelta}"
        )
    return x


def randtimedelta(a: timedelta, b: timedelta | None = None) -> str:
    if b is None:
        a, b = (timedelta(0), a)
    a = parsetimedelta(a)
    b = parsetimedelta(b)
    seconds = randint(int(a.total_seconds()), int(b.total_seconds()))  # noqa: S311
    return str(timedelta(seconds=seconds))


def snap_to_grid(
    point: float, grid_spacing: float = 10, grid_reference: float = 0
) -> float:
    return grid_reference + grid_spacing * round(
        (point - grid_reference) / grid_spacing
    )


def bits(n: int | str) -> str:
    # https://stackoverflow.com/a/4859937
    if isinstance(n, str):
        n = int(n, 16)
    return bin(n)[2:].zfill(8)


def lines(file: str) -> list[str]:
    with open(file) as f:
        return [x.strip() for x in f.readlines()]


_SIZE_SUFFIXES = (
    ('TiB', 1024 * 1024 * 1024 * 1024),
    ('GiB', 1024 * 1024 * 1024),
    ('MiB', 1024 * 1024),
    ('KiB', 1024),
    ('TB', 1000 * 1000 * 1000 * 1000),
    ('GB', 1000 * 1000 * 1000),
    ('MB', 1000 * 1000),
    ('KB', 1000),
    ('T', 1024 * 1024 * 1024 * 1024),
    ('G', 1024 * 1024 * 1024),
    ('M', 1024 * 1024),
    ('K', 1024),
    ('B', 1),
)


def parse_size(size: str) -> float | int:
    for suffix, multiplier in _SIZE_SUFFIXES:
        if size.endswith(suffix):
            suffix_len = len(suffix)
            return float(size[:-suffix_len]) * multiplier
    return int(size)


def format_size(size: float) -> str:
    for suffix, multiplier in _SIZE_SUFFIXES:
        if size > multiplier:
            size_in_units = size / multiplier
            if size_in_units >= 999.5:
                # fix for returns like "1.03e3"
                return f"{int(size_in_units)} {suffix}"
            return f"{size_in_units:.3g} {suffix}"
    return f"{size}"
