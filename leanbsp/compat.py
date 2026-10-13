"""Compatibility between an application, a BSP target and the contract lean-bsp knows."""

from importlib.resources import files

from leanbsp.errors import LeanBspError
from leanbsp.manifest import App, Bsp, Target


def supported_contracts() -> list[str]:
    return sorted(d.name for d in (files('leanbsp') / 'data' / 'contract').iterdir() if d.is_dir())


def canonical_header(contract_version: str) -> bytes:
    return (files('leanbsp') / 'data' / 'contract' / contract_version / 'bsp_api.h').read_bytes()


def bsp_problems(bsp: Bsp) -> list[str]:
    """What is wrong with the BSP itself, independently of any application."""
    if bsp.contract_version not in supported_contracts():
        return [
            f'BSP {bsp.name} implements contract {bsp.contract_version}, which this lean-bsp does not know '
            f'(known: {", ".join(supported_contracts())})'
        ]
    header = bsp.root / 'include' / 'bsp_api.h'
    if not header.is_file():
        return [f'BSP {bsp.name} has no include/bsp_api.h']
    if header.read_bytes() != canonical_header(bsp.contract_version):
        return [f'{header} differs from the canonical bsp_api.h of contract {bsp.contract_version}']
    return []


def problems(app: App, bsp: Bsp, target: Target) -> list[str]:
    found = bsp_problems(bsp)
    if app.contract_version != bsp.contract_version:
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
