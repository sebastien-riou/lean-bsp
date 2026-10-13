"""lean-bsp: build, load and run applications against generic, reusable BSPs."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version('lean-bsp')
except PackageNotFoundError:  # running from a source checkout that was never installed
    __version__ = '0+unknown'
