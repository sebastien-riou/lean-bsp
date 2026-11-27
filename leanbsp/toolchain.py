"""Which compiler a target builds with, as every BSP finds it.

A target's `toolchain` is a GNU triple (arm-none-eabi, riscv-none-elf, ...) or, for a native target,
`gcc`. For a triple, CROSS_COMPILE names the toolchain's prefix and must then end with that triple
(e.g. /opt/.../bin/arm-none-eabi-); without it, <triple>-gcc is taken from PATH. A native target
takes CC, by default gcc, from PATH.

`lbsp capabilities` reports the result, so that an application building its own archive
([app.build]) compiles and archives it with the very compiler the BSP links it with: objects built
for link-time optimization link only with the GCC that wrote them.
"""

import os
import shutil
from dataclasses import dataclass

from leanbsp.manifest import Target

NATIVE = 'gcc'


class ToolchainError(Exception):
    """The target's toolchain cannot be found, with what to do about it."""


@dataclass
class Tools:
    cc: str | None  # the C compiler, an absolute path when found
    ar: str | None  # gcc-ar, the archiver that indexes link-time optimization objects
    problem: str | None  # why they were not found


def cross_prefix(target: Target) -> str:
    """The prefix of the target's toolchain, e.g. /opt/.../bin/arm-none-eabi-."""
    triple = target.toolchain
    prefix = os.environ.get('CROSS_COMPILE')
    if prefix and not prefix.endswith(f'{triple}-'):
        raise ToolchainError(f"target {target.name} needs a {triple} toolchain, but CROSS_COMPILE is {prefix}: set "
                             f"it to that toolchain's prefix, or unset it to take {triple}-gcc from PATH")
    prefix = prefix or f'{triple}-'
    if shutil.which(f'{prefix}gcc') is None:
        raise ToolchainError(f'no {prefix}gcc: install a {triple} toolchain, and name it with CROSS_COMPILE or put '
                             f'it in PATH')
    return prefix


def _absolute(command: str) -> str | None:
    found = shutil.which(command)
    return os.path.abspath(found) if found else None  # not resolved: xpack's bin/ links into bin-org/


def tools(target: Target) -> Tools:
    if target.toolchain == NATIVE:
        cc = _absolute(os.environ.get('CC', 'gcc'))
        return Tools(cc=cc, ar=_absolute('gcc-ar'), problem=None if cc else 'no C compiler: install gcc or set CC')
    try:
        prefix = cross_prefix(target)
    except ToolchainError as e:
        return Tools(cc=None, ar=None, problem=str(e))
    return Tools(cc=_absolute(f'{prefix}gcc'), ar=_absolute(f'{prefix}gcc-ar'), problem=None)
