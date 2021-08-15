#!/usr/bin/env xonsh

def snap_to_grid(point, grid_spacing=10, grid_reference=0):
     return grid_reference + grid_spacing * round((point - grid_reference) / grid_spacing)
