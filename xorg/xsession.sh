#!/bin/bash

export _JAVA_AWT_WM_NONREPARENTING=1

cmd_exists() {
    for cmd in "$@"; do
        if ! command -v "$1" >/dev/null 2>&1; then
            return 1
        fi
    done
    return 0
}

first_cmd() {
    for cmd in "$@"; do
        if cmd_exists "$cmd"; then
            echo "$cmd"
            return 0
        fi
    done
    return 1
}

cmd_exists qt5ct && export QT_QPA_PLATFORMTHEME=qt5ct

if editor="$(first_cmd subl3 subl)"; then
    export EDITOR="$editor -nw"
fi

if browser="$(first_cmd librewolf firefox chromium google-chrome-stable)"; then
    export BROWSER="$browser"
fi

export TERMINAL=xterm
if terminal="$(first_cmd alacritty kitty urxvt)"; then
    export TERMINAL="$terminal"
fi

[ -f '/etc/X11/xinit/.Xresources' ] && xrdb -merge '/etc/X11/xinit/.Xresources'
[ -f "$HOME/.Xresources" ] && xrdb -merge "$HOME/.Xresources"

cmd_exists numlockx && numlockx on
cmd_exists i3status xmodar && i3status | xmobar &
cmd_exists xbindkeys && xbindkeys

if cmd_exists xautolock i3lock; then
    LOCKER='i3lock -fc000000 --ring-color=b5890080 --keyhl-color=b58900 --bshl-color=dc322f --verif-text= --insidever-color=00000000 --ringver-color=859900 --wrong-color=dc322f --insidewrong-color=00000000 --ringwrong-color=dc322f'
    xautolock -time 10 -detectsleep -locker "$LOCKER" &
    cmd_exists caffeine && caffeine &
fi

if [ -d '/etc/X11/xinit/xinitrc.d' ] ; then
    for f in /etc/X11/xinit/xinitrc.d/?*.sh ; do
        [ -x "$f" ] && . "$f"
    done
    unset f
fi

# These lines race on some systems. No idea why.
xsetroot -cursor_name left_ptr
# TODO figure out how to combine this with the below: setxkbmap -option ctrl:nocaps
xkbcomp -I"$HOME/.wlrenv/keyboard/xkb" "$HOME/.wlrenv/keyboard/xkb/pc.xkb" "$DISPLAY"
# TODO this started causing some hosts to render all windows as pure black, no idea why
#xrandr --dpi 96

cmd_exists xcape && xcape

if cmd_exists redshift; then
    redshift &
    redshift_pid=$!
fi

wlr-autorandr

if [ "$HOME/.xmonad/xmonad-x86_64-linux" -ot "$(which xmonad)" ]; then
    xmonad --recompile
fi

if [ -f "$HOME/.xmonad/xmonad.state" ]; then
    printf 'Removing malignant xmonad.state file...\n'
    rm "$HOME/.xmonad/xmonad.state"
fi

systemctl --user import-environment PATH DBUS_SESSION_BUS_ADDRESS
systemctl --no-block --user start xsession.target

if cmd_exists tmux; then
    wlr-fix-tmux-resurrect
    wlr-ensure-tmux-running
    wlr-open-tmux-sessions
fi

if [ -n "${XBACKLIGHT:-}" ] && cmd_exists xbacklight; then
    xbacklight -set "$XBACKLIGHT"
fi

if [ -n "$NO_DPMS" ]; then
    xset s off -dpms
fi

if [ "${BARRIER_SERVER:-}" == localhost ]; then
    if cmd_exists barriers; then
        # if barriers launches before xmonad, Xorg hangs (!) when barrier takes control
        # should probably make xmonad launch this kind of thing
        ( sleep 10; barriers --enable-crypto ) &
    fi
elif [ -n "${BARRIER_SERVER:-}" ]; then
    if cmd_exists barrierc; then
        # if barriers launches before xmonad, Xorg hangs (!) when barrier takes control
        # should probably make xmonad launch this kind of thing
        ( sleep 10; barrierc --enable-crypto "$BARRIER_SERVER" ) &
    fi
fi

xmonad

systemctl --no-block --user stop xsession.target

[ -n "${redshift_pid:-}" && kill "$redshift_pid"
