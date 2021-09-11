#!/usr/bin/env xonsh

import random


def coin():
    return 'heads' if random.randint(0, 1) else 'tails'


def snap_to_grid(point, grid_spacing=10, grid_reference=0):
     return grid_reference + grid_spacing * round((point - grid_reference) / grid_spacing)
