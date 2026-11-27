"""The entry point of a board BSP, around a description of the board and of its build.

A board BSP's bsp_cli.py describes its board (Board) and how it builds (a function from the
application, the target and the build directory to the artifact), then calls main(), which answers
the four verbs of the entry-point protocol:

- capabilities: nothing beyond bsp.toml;
- build: the BSP's build, recorded in the build directory's state;
- load and run: through lean-hwbridge (`hwbridge`, or the command in HWBRIDGE), so they work from a
  sandboxed session as well as from a terminal. The board is the usbman nickname in HWBRIDGE_BOARD,
  by default the BSP's name. Without a bridge, they report what to do by hand ("manual").

Every verb ends its output with one JSON line, the result lean-bsp parses.
"""

import argparse
import json
import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from leanbsp.errors import LeanBspError
from leanbsp.manifest import Bsp, Target, load_bsp

STATE = 'bsp-state.json'  # in the build directory: what was built, where the boot output starts


class BuildError(Exception):
    """A build failure the BSP explains, reported as the error's message."""


@dataclass
class Board:
    root: Path           # the BSP repository
    title: str           # the board, as its vendor names it
    flash_by_hand: str   # how to flash without a bridge: {elf} and {hex} are the build's images
    run_by_hand: str     # how to start the firmware and read UART 0 without a bridge


# (the application's app.json, the target, the build directory) -> artifact: {"elf", "bin", "hex"}
Build = Callable[[dict, Target, Path], dict]


def result(status: str, **fields) -> int:
    print(json.dumps({'status': status, **fields}), flush=True)
    return 1 if status == 'error' else 0


def read_state(bdir: Path) -> dict:
    path = Path(bdir) / STATE
    return json.loads(path.read_text()) if path.is_file() else {}


def write_state(bdir: Path, **fields) -> None:
    (Path(bdir) / STATE).write_text(json.dumps({**read_state(bdir), **fields}) + '\n')


# ---- build ----


def _build(bsp: Bsp, build: Build, args) -> int:
    app = json.loads(Path(args.app_json).read_text())
    bdir = Path(args.bdir).resolve()
    bdir.mkdir(parents=True, exist_ok=True)
    try:
        artifact = build(app, bsp.target(args.target), bdir)
    except (BuildError, LeanBspError) as e:
        return result('error', message=str(e))
    write_state(bdir, elf=artifact['elf'], hex=artifact.get('hex'))
    return result('ok', artifact=artifact)


# ---- load and run, through lean-hwbridge ----


class NoBridge(Exception):
    pass


def _hwbridge(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = [*shlex.split(os.environ.get('HWBRIDGE', 'hwbridge')), *args]
    print(shlex.join(cmd), flush=True)
    return subprocess.run(cmd, capture_output=capture, text=True)


def _bridge_json(*args: str) -> dict:
    try:
        done = _hwbridge(*args, '--json')
    except FileNotFoundError:
        raise NoBridge('hwbridge is not installed') from None
    if done.returncode == 3:
        raise NoBridge(done.stderr.strip())
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f'hwbridge {args[0]} failed ({done.returncode})')
    return json.loads(done.stdout) if done.stdout.strip() else {}


def _load(bsp: Bsp, board: Board, args) -> int:
    bdir = Path(args.bdir)
    state = read_state(bdir)
    if 'elf' not in state:
        return result('error', message=f'nothing built in {bdir}')
    nickname = os.environ.get('HWBRIDGE_BOARD', bsp.name)
    try:
        _bridge_json('serial', 'open', nickname)  # first, so that the boot output is captured
        flashed = _bridge_json('flash', nickname, state['elf'])
    except NoBridge as e:
        by_hand = board.flash_by_hand.format(elf=state['elf'], hex=state.get('hex'))
        return result('manual', message=f'{e}. Flash it yourself, e.g. {by_hand}; or start `hwbridge serve`')
    except RuntimeError as e:
        return result('error', message=str(e))
    write_state(bdir, boot=[flashed['generation'], flashed['offset']])
    return result('ok', boot=f'{flashed["generation"]}:{flashed["offset"]}')


def _run(bsp: Bsp, board: Board, args) -> int:
    nickname = os.environ.get('HWBRIDGE_BOARD', bsp.name)
    com_device = f'hwbridge:{nickname}'
    try:
        _bridge_json('serial', 'open', nickname)
        reset = _bridge_json('reset', nickname)
    except NoBridge as e:
        return result('manual', message=f'{e}. {board.run_by_hand}')
    except RuntimeError as e:
        return result('error', message=str(e))
    boot = f'{reset["generation"]}:{reset["offset"]}'
    if args.detach:
        return result('ok', com_device=com_device, exit_code=None, boot=boot)
    # follow the console: a board has no exit code, so this lasts until --until, --timeout or Ctrl-C
    cat = ['serial', 'cat', nickname, '--from', boot]
    if args.until:
        cat += ['--until', args.until]
    if args.timeout is not None and args.timeout >= 0:
        cat += ['--timeout', str(args.timeout)]
    done = _hwbridge(*cat, capture=False)
    print(flush=True)
    if done.returncode == 124 and args.until:
        return result('error', message=f'{args.until!r} did not show up within {args.timeout:g} s',
                      com_device=com_device, exit_code=None)
    if done.returncode not in (0, 124):
        return result('error', message=f'following the console failed ({done.returncode})',
                      com_device=com_device, exit_code=None)
    return result('ok', com_device=com_device, exit_code=None, boot=boot)


# ---- the verbs ----


def main(board: Board, build: Build, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='bsp_cli.py', description=f'{board.title} BSP entry point for lean-bsp')
    sub = parser.add_subparsers(dest='verb', required=True)
    p = sub.add_parser('capabilities')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('build')
    p.add_argument('--app-json', required=True)
    p.add_argument('--target', required=True)
    p.add_argument('--bdir', required=True)
    p = sub.add_parser('load')
    p.add_argument('--bdir', required=True)
    p.add_argument('--target')
    p = sub.add_parser('run')
    p.add_argument('--bdir', required=True)
    p.add_argument('--target')
    p.add_argument('--detach', action='store_true')
    p.add_argument('--timeout', type=float, help='seconds to follow the console; -1 or absent: until Ctrl-C')
    p.add_argument('--until', help='stop following once this text was printed')
    args = parser.parse_args(argv)
    try:
        bsp = load_bsp(board.root)
    except LeanBspError as e:
        return result('error', message=str(e))
    if args.verb == 'capabilities':
        return result('ok')
    if args.verb == 'build':
        return _build(bsp, build, args)
    if args.verb == 'load':
        return _load(bsp, board, args)
    return _run(bsp, board, args)
