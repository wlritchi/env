filetype plugin indent on
set background=dark
syntax on
set nocompatible
set showcmd
set showmatch
set ignorecase
set smartcase
set incsearch
set autowrite
set hidden
set expandtab
set tabstop=4
set shiftwidth=4

" allow backspace always (default is certifiably insane)
set backspace=indent,eol,start

" true color
let &t_8f="\<Esc>[38;2;%lu;%lu;%lum"
let &t_8b="\<Esc>[48;2;%lu;%lu;%lum"
set termguicolors
colorscheme solarized8

if &term =~ "tmux*"
    " Activate bracketed paste in tmux
    " https://vi.stackexchange.com/a/25346
    let &t_BE = "\e[?2004h"
    let &t_BD = "\e[?2004l"
    exec "set t_PS=\e[200~"
    exec "set t_PE=\e[201~"

    " Fix Ctrl-bindings in tmux
    " https://superuser.com/a/1513950/172230
    map  <Esc>[1;5A <C-Up>
    map  <Esc>[1;5B <C-Down>
    map  <Esc>[1;5D <C-Left>
    map  <Esc>[1;5C <C-Right>
    cmap <Esc>[1;5A <C-Up>
    cmap <Esc>[1;5B <C-Down>
    cmap <Esc>[1;5D <C-Left>
    cmap <Esc>[1;5C <C-Right>
    map  <Esc>[1;2D <S-Left>
    map  <Esc>[1;2C <S-Right>
    cmap <Esc>[1;2D <S-Left>
    cmap <Esc>[1;2C <S-Right>
endif
