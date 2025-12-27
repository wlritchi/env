from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ptpython.repl import PythonRepl

__all__ = ['configure']


def configure(repl: "PythonRepl") -> None:
    repl.vi_mode = True
    repl.use_code_colorscheme('solarized-dark')
