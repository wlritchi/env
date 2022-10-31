#!/usr/bin/env xonsh

import base64
import json
import random
from datetime import datetime, timedelta, timezone
from itertools import product
from os.path import basename, dirname, exists, lexists, isabs, isfile, isdir, islink, ismount, realpath, relpath, samefile
from random import randint


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
