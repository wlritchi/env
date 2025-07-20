__all__ = ['configure']


def configure(repl) -> None:  # noqa: ANN001
    repl.vi_mode = True
    repl.use_code_colorscheme('solarized-dark')
