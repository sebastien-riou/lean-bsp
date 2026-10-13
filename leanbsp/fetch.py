"""Clone a registry BSP as a sibling repository, then run its setup steps."""

import re
import shlex
import subprocess
from pathlib import Path

from leanbsp.errors import LeanBspError, info, warn
from leanbsp.manifest import load_bsp
from leanbsp.registry import RegistryEntry


def _git(args: list[str], cwd: Path | None = None, *, capture: bool = False) -> str:
    try:
        done = subprocess.run(['git', *args], cwd=cwd, check=False, text=True, capture_output=capture)
    except FileNotFoundError:
        raise LeanBspError('git is not installed') from None
    if done.returncode != 0:
        detail = f': {done.stderr.strip()}' if capture and done.stderr else ''
        raise LeanBspError(f'git {" ".join(args)} failed{detail}')
    return done.stdout if capture else ''


def _looks_like_sha(ref: str) -> bool:
    return re.fullmatch(r'[0-9a-f]{7,40}', ref) is not None


def _clone(entry: RegistryEntry, dest: Path, ref: str | None) -> None:
    info(f'cloning {entry.git} into {dest}' + (f' at {ref}' if ref else ''))
    if ref and not _looks_like_sha(ref):
        _git(['clone', '--branch', ref, entry.git, str(dest)])
    else:
        _git(['clone', entry.git, str(dest)])
        if ref:
            _git(['checkout', ref], cwd=dest)


def _check_existing(dest: Path, ref: str | None) -> None:
    if not (dest / '.git').exists():
        raise LeanBspError(f'{dest} exists but is not a git clone; move it away or pass --dir')
    info(f'{dest} already exists, skipping the clone')
    if _git(['status', '--porcelain'], cwd=dest, capture=True).strip():
        warn(f'{dest} has local changes')
    if ref:
        head = _git(['rev-parse', 'HEAD'], cwd=dest, capture=True).strip()
        wanted = None
        for candidate in (ref, f'origin/{ref}'):  # a branch may exist only as a remote-tracking one
            try:
                wanted = _git(['rev-parse', f'{candidate}^{{commit}}'], cwd=dest, capture=True).strip()
                break
            except LeanBspError:
                continue
        if wanted is None:
            warn(f'{dest}: ref {ref!r} is unknown in this clone')
        elif head != wanted:
            warn(f'{dest} is not at {ref}')


def run_setup_steps(dest: Path) -> None:
    bsp = load_bsp(dest)
    for step in bsp.setup_steps:
        info(f'setup {bsp.name}: {step}')
        done = subprocess.run(shlex.split(step), cwd=dest, check=False)
        if done.returncode != 0:
            raise LeanBspError(f'setup step {step!r} of BSP {bsp.name} failed with code {done.returncode}')


def fetch(entry: RegistryEntry, dest: Path, ref: str | None) -> Path:
    """Clone (or reuse) `dest`, then run the BSP's idempotent setup steps."""
    ref = ref or entry.default_ref
    if dest.exists():
        _check_existing(dest, ref)
    else:
        _clone(entry, dest, ref)
    run_setup_steps(dest)
    return dest
