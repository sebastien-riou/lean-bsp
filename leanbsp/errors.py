import sys


class LeanBspError(Exception):
    """An error reported to the user as is, without a traceback."""


def warn(message: str) -> None:
    print(f'lean-bsp: warning: {message}', file=sys.stderr)


def info(message: str) -> None:
    """lean-bsp's own messages go to stderr, so stdout ends with the BSP's JSON result line."""
    print(f'lean-bsp: {message}', file=sys.stderr)
