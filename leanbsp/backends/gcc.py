"""A plain gcc build for a board BSP: its sources and the application's, compiled in parallel and
linked with one linker script, then the HEX and BIN images flashing tools take.

The CPU flags are the target's (bsp.toml); the toolchain is the target's too: CROSS_COMPILE names
its prefix, and must then be one for the target's triple, or else <triple>- is taken from PATH
(leanbsp.toolchain). An application that builds its own archive ([app.build]) has it linked after
its sources, with the link flags it asks for.
"""

import hashlib
import os
import shlex
import subprocess
import sys
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from leanbsp import toolchain
from leanbsp.backends.board import BuildError
from leanbsp.manifest import Target

OPTIMISATION = ('-Og', '-g')  # the default: debuggable, and what the cycle counts in the READMEs were taken with

# contract 1.4: the heap use is counted by the BSP's copy of lean-bsp's bsp_heap.c, which wraps these
HEAP_WRAP_FLAGS = ('-Wl,--wrap=malloc,--wrap=calloc,--wrap=realloc,--wrap=free',)


def cross_prefix(target: Target) -> str:
    """The prefix of the target's toolchain, e.g. /opt/.../bin/arm-none-eabi-."""
    try:
        return toolchain.cross_prefix(target)
    except toolchain.ToolchainError as e:
        raise BuildError(str(e)) from None


def app_link_inputs(app: dict) -> list[str]:
    """What the application adds to the link after its sources' objects: its own archive, if it
    builds one ([app.build]), and the flags that archive needs."""
    return [*app.get('link_flags', []), *([app['lib']] if app.get('lib') else [])]


def build(app: dict, target: Target, bdir: Path, *, root: Path, sources: Iterable[Path],
          include_dirs: Iterable[Path], defines: Iterable[str], linker_script: Path,
          link_flags: Iterable[str] = (), optimisation: Iterable[str] = OPTIMISATION,
          board_flags: Iterable[str] = ()) -> dict:
    """Compile and link; the application's own linker script, if any, replaces `linker_script`.

    Objects are linked in the order of `sources`, then the application's: a BSP whose startup code
    must come first lists it first. `optimisation` replaces -Og -g, for a board whose code must fit
    a small memory. `board_flags` go to the BSP's sources only, not to the application's: what vendor
    code needs to compile as its vendor builds it (a C standard, the signedness of char)."""
    prefix = cross_prefix(target)
    cc = f'{prefix}gcc'
    obj_dir = bdir / 'obj'
    obj_dir.mkdir(parents=True, exist_ok=True)
    common = [*target.cflags, *optimisation, '-Wall', '-fdata-sections', '-ffunction-sections']
    board = [*common, *(f'-D{d}' for d in defines), *(f'-I{d}' for d in include_dirs)]
    application = [*board, *(f'-I{d}' for d in app['include_dirs']), *(f'-D{d}' for d in app['defines'])]
    app_sources = [Path(s) for s in app['sources']]
    units = [(Path(s), [*board, *board_flags]) for s in sources] + [(s, application) for s in app_sources]

    def compile_one(unit):
        src, flags = unit
        obj = obj_dir / f'{src.stem}-{hashlib.sha1(str(src).encode()).hexdigest()[:8]}.o'  # same name in two dirs
        language = ['-x', 'assembler-with-cpp'] if src.suffix in ('.s', '.S') else []
        cmd = [cc, *language, *flags, '-c', str(src), '-o', str(obj)]
        return src, obj, cmd, subprocess.run(cmd, capture_output=True, text=True)

    objects = []
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for src, obj, cmd, done in pool.map(compile_one, units):
            shown = shlex.join(cmd) if src in app_sources else f'cc {src.relative_to(root)}'
            print(shown, flush=True)
            sys.stdout.write(done.stdout + done.stderr)
            if done.returncode != 0:
                raise BuildError(f'compiling {src} failed')
            objects.append(str(obj))
    script = Path(app['linker_script']) if app.get('linker_script') else linker_script
    elf = bdir / f'{app["name"]}.elf'
    link = [cc, *target.cflags, '-specs=nano.specs', f'-T{script}', *link_flags, *objects, *app_link_inputs(app),
            '-Wl,--gc-sections',
            f'-Wl,-Map={bdir / (app["name"] + ".map")},--cref', '-lc', '-lm', '-lnosys', '-o', str(elf)]
    print(shlex.join(link), flush=True)
    if subprocess.run(link).returncode != 0:
        raise BuildError('linking failed')
    hex_file, bin_file = elf.with_suffix('.hex'), elf.with_suffix('.bin')
    for fmt, out in (('ihex', hex_file), ('binary', bin_file)):
        if subprocess.run([f'{prefix}objcopy', '-O', fmt, str(elf), str(out)]).returncode != 0:
            raise BuildError(f'objcopy -O {fmt} failed')
    subprocess.run([f'{prefix}size', str(elf)])
    return {'elf': str(elf), 'bin': str(bin_file), 'hex': str(hex_file)}
