#!/usr/bin/env xonsh

from datetime import datetime, timedelta, timezone
import random


def coin():
    return 'heads' if random.randint(0, 1) else 'tails'


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
    seconds = random.randint(int(a.total_seconds()), int(b.total_seconds()))
    return str(timedelta(seconds=seconds))


def snap_to_grid(point, grid_spacing=10, grid_reference=0):
     return grid_reference + grid_spacing * round((point - grid_reference) / grid_spacing)
