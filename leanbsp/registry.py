"""Known-BSP registries: the built-in one shipped with lean-bsp, plus extra files merged on top."""

import os
import tomllib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from leanbsp.errors import LeanBspError
from leanbsp.manifest import load_toml


@dataclass
class RegistryEntry:
    name: str
    git: str
    description: str = ''
    tags: list[str] = field(default_factory=list)
    default_ref: str | None = None
    source: str = ''  # which registry file this entry came from


def _parse(data: dict, source: str) -> list[RegistryEntry]:
    entries = []
    for raw in data.get('bsp', []):
        if not isinstance(raw, dict) or not isinstance(raw.get('name'), str) or not isinstance(raw.get('git'), str):
            raise LeanBspError(f'{source}: every [[bsp]] entry needs a string name and git')
        entries.append(
            RegistryEntry(
                name=raw['name'],
                git=raw['git'],
                description=raw.get('description', ''),
                tags=list(raw.get('tags', [])),
                default_ref=raw.get('default_ref'),
                source=source,
            )
        )
    return entries


def extra_registry_files() -> list[Path]:
    """~/.config/lean-bsp/registry.d/*.toml, then each file listed in LEAN_BSP_REGISTRY (os.pathsep-separated)."""
    config_home = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    paths = sorted((config_home / 'lean-bsp' / 'registry.d').glob('*.toml'))
    paths += [Path(p) for p in os.environ.get('LEAN_BSP_REGISTRY', '').split(os.pathsep) if p]
    return paths


def load_registry() -> dict[str, RegistryEntry]:
    """All known BSPs by name; a later registry file wins on a name collision."""
    builtin = files('leanbsp') / 'data' / 'registry.toml'
    entries = _parse(tomllib.loads(builtin.read_text(encoding='utf-8')), 'built-in')
    for path in extra_registry_files():
        entries += _parse(load_toml(path), str(path))
    return {e.name: e for e in entries}
