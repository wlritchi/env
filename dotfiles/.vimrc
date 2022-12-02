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

" Activate bracketed paste in tmux
" https://vi.stackexchange.com/a/25346
if &term =~ "tmux*"
    let &t_BE = "\e[?2004h"
    let &t_BD = "\e[?2004l"
    exec "set t_PS=\e[200~"
    exec "set t_PE=\e[201~"
endif
