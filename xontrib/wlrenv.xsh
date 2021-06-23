#!/usr/bin/env xonsh

from xonsh.ansi_colors import register_custom_ansi_style
from xonsh.pyghooks import pygments_version_info, register_custom_pygments_style

xontrib load argcomplete pipeliner whole_word_jumping

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

del solarized
# del style

$PROMPT = '{YELLOW}{localtime}{GREEN}{user}@{hostname}{BLUE}{cwd}{RESET}$ '
