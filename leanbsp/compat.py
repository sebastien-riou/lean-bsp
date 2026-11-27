"""Compatibility between an application, a BSP target and the contract lean-bsp knows."""

from importlib.resources import files

from leanbsp.errors import LeanBspError
from leanbsp.manifest import App, Bsp, Target


def supported_contracts() -> list[str]:
    return sorted(d.name for d in (files('leanbsp') / 'data' / 'contract').iterdir() if d.is_dir())


def contract_file(contract_version: str, name: str) -> bytes:
    return (files('leanbsp') / 'data' / 'contract' / contract_version / name).read_bytes()


def canonical_header(contract_version: str) -> bytes:
    return contract_file(contract_version, 'bsp_api.h')


def contract_files(contract_version: str) -> dict[str, str]:
    """The files a BSP keeps byte-identical copies of, for its contract version: where each goes in
    the BSP, from its name in the contract. A header goes to include/, a C file to src/ (bsp_heap.c,
    since 1.4)."""
    shipped = sorted(f.name for f in (files('leanbsp') / 'data' / 'contract' / contract_version).iterdir()
                     if f.is_file())
    return {f'{"src" if name.endswith(".c") else "include"}/{name}': name for name in shipped}


def bsp_problems(bsp: Bsp) -> list[str]:
    """What is wrong with the BSP itself, independently of any application."""
    if bsp.contract_version not in supported_contracts():
        return [
            f'BSP {bsp.name} implements contract {bsp.contract_version}, which this lean-bsp does not know '
            f'(known: {", ".join(supported_contracts())})'
        ]
    found = []
    for where, name in contract_files(bsp.contract_version).items():
        copy = bsp.root / where
        if not copy.is_file():
            found.append(f'BSP {bsp.name} has no {where}')
        elif copy.read_bytes() != contract_file(bsp.contract_version, name):
            found.append(f'{copy} differs from the canonical {name} of contract {bsp.contract_version}')
    return found


def contract_serves(bsp_version: str, app_version: str) -> bool:
    """A contract adds only within a major version, so a BSP serves applications written for its own
    version or an earlier minor version of the same major."""
    try:
        bsp_major, bsp_minor = (int(n) for n in bsp_version.split('.'))
        app_major, app_minor = (int(n) for n in app_version.split('.'))
    except ValueError:
        return bsp_version == app_version
    return bsp_major == app_major and bsp_minor >= app_minor


def problems(app: App, bsp: Bsp, target: Target) -> list[str]:
    found = bsp_problems(bsp)
    if not contract_serves(bsp.contract_version, app.contract_version):
        found.append(
            f'{app.name} targets contract {app.contract_version} but BSP {bsp.name} implements {bsp.contract_version}'
        )
    missing = sorted(k for k, required in app.requires.items() if required and not target.primitives.get(k, False))
    if missing:
        found.append(
            f'BSP {bsp.name} target {target.name} lacks primitive(s) required by {app.name}: {", ".join(missing)}'
        )
    return found


def check(app: App, bsp: Bsp, target: Target) -> None:
    found = problems(app, bsp, target)
    if found:
        raise LeanBspError('incompatible application and BSP:\n  ' + '\n  '.join(found))
