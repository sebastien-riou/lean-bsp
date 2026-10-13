"""Loaders and validators for bsp.toml and app.toml."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from leanbsp.errors import LeanBspError

RUN_MODES = ('native-process', 'auto-after-load', 'debugger-attach', 'manual', 'emulator')
PRIMITIVES = (
    'uart',
    'led',
    'button',
    'cpu_cycle_counter',
    'cpu_frequency',
    'cache_clear',
    'heap_usage',
    'flash_read',
    'flash_erase',
    'flash_write',
)

_MISSING = object()


def load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open('rb') as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise LeanBspError(f'{path} not found') from None
    except tomllib.TOMLDecodeError as e:
        raise LeanBspError(f'{path}: {e}') from None


def _get(table: dict, key: str, path: Path, where: str, kind: type | tuple = str, default: Any = _MISSING) -> Any:
    if key not in table:
        if default is _MISSING:
            raise LeanBspError(f'{path}: missing {where}.{key}')
        return default
    value = table[key]
    if not isinstance(value, kind):
        names = ' or '.join(k.__name__ for k in kind) if isinstance(kind, tuple) else kind.__name__
        raise LeanBspError(f'{path}: {where}.{key} must be a {names}')
    return value


def _str_list(table: dict, key: str, path: Path, where: str, default: Any = _MISSING) -> list[str]:
    value = _get(table, key, path, where, list, default)
    if not all(isinstance(v, str) for v in value):
        raise LeanBspError(f'{path}: {where}.{key} must be a list of strings')
    return list(value)


def _primitives(table: dict, path: Path, where: str) -> dict[str, bool]:
    unknown = sorted(set(table) - set(PRIMITIVES))
    if unknown:
        raise LeanBspError(
            f'{path}: unknown primitive(s) in {where}: {", ".join(unknown)}; known: {", ".join(PRIMITIVES)}'
        )
    for key, value in table.items():
        if not isinstance(value, bool):
            raise LeanBspError(f'{path}: {where}.{key} must be true or false')
    return dict(table)


@dataclass
class Target:
    name: str
    cpu: str
    toolchain: str
    cflags: list[str]
    linker_fragments: list[str]
    run_mode: str
    core_id: str | None
    primitives: dict[str, bool]  # effective: BSP defaults merged with this target's override


@dataclass
class Bsp:
    root: Path
    name: str
    contract_version: str
    version: str
    description: str
    entry_point: Path
    requires_tools: list[str]
    setup_steps: list[str]
    primitives: dict[str, bool]
    targets: list[Target]

    def target(self, name: str | None) -> Target:
        """The target named `name`, or the only one when `name` is None."""
        names = ', '.join(t.name for t in self.targets)
        if name is None:
            if len(self.targets) == 1:
                return self.targets[0]
            raise LeanBspError(f'BSP {self.name} offers several targets, pick one with --target: {names}')
        for t in self.targets:
            if t.name == name:
                return t
        raise LeanBspError(f'BSP {self.name} has no target {name!r}; available: {names}')


def load_bsp(root: Path) -> Bsp:
    path = root / 'bsp.toml'
    bsp = _get(load_toml(path), 'bsp', path, '', dict)
    name = _get(bsp, 'name', path, 'bsp')
    build = _get(bsp, 'build', path, 'bsp', dict, {})
    setup = _get(bsp, 'setup', path, 'bsp', dict, {})
    defaults = {p: False for p in PRIMITIVES}
    defaults.update(_primitives(_get(bsp, 'primitives', path, 'bsp', dict, {}), path, 'bsp.primitives'))

    cores = {}
    for core in _get(bsp, 'cores', path, 'bsp', list, []):
        core_id = _get(core, 'id', path, 'bsp.cores')
        cores[core_id] = {
            'cpu': _get(core, 'cpu', path, f'bsp.cores[{core_id}]', str, None),
            'cflags': _str_list(core, 'cflags', path, f'bsp.cores[{core_id}]', None),
        }

    targets: list[Target] = []
    for t in _get(bsp, 'targets', path, 'bsp', list):
        tname = _get(t, 'name', path, 'bsp.targets')
        where = f'bsp.targets[{tname}]'
        if any(existing.name == tname for existing in targets):
            raise LeanBspError(f'{path}: duplicate target name {tname!r}')
        core_id = _get(t, 'core_id', path, where, str, None)
        core = {'cpu': None, 'cflags': None}
        if core_id is not None:
            if core_id not in cores:
                raise LeanBspError(f'{path}: {where}.core_id {core_id!r} is not declared in [[bsp.cores]]')
            core = cores[core_id]
        cpu = _get(t, 'cpu', path, where, str, core['cpu'])
        if cpu is None:
            raise LeanBspError(f'{path}: missing {where}.cpu')
        cflags = _str_list(t, 'cflags', path, where, core['cflags'] if core['cflags'] is not None else [])
        run_mode = _get(t, 'run_mode', path, where)
        if run_mode not in RUN_MODES:
            raise LeanBspError(f'{path}: {where}.run_mode must be one of {", ".join(RUN_MODES)}')
        override_table = _get(t, 'primitives_override', path, where, dict, {})
        override = _primitives(override_table, path, f'{where}.primitives_override')
        targets.append(
            Target(
                name=tname,
                cpu=cpu,
                toolchain=_get(t, 'toolchain', path, where),
                cflags=cflags,
                linker_fragments=_str_list(t, 'linker_fragments', path, where, []),
                run_mode=run_mode,
                core_id=core_id,
                primitives={**defaults, **override},
            )
        )
    if not targets:
        raise LeanBspError(f'{path}: [[bsp.targets]] is empty')

    return Bsp(
        root=root,
        name=name,
        contract_version=_get(bsp, 'lean_bsp_contract_version', path, 'bsp'),
        version=_get(bsp, 'version', path, 'bsp', str, '0.0.0'),
        description=_get(bsp, 'description', path, 'bsp', str, ''),
        entry_point=root / _get(bsp, 'entry_point', path, 'bsp'),
        requires_tools=_str_list(build, 'requires_tools', path, 'bsp.build', []),
        setup_steps=_str_list(setup, 'steps', path, 'bsp.setup', []),
        primitives=defaults,
        targets=targets,
    )


@dataclass
class App:
    root: Path
    name: str
    contract_version: str
    version: str
    default_bsp: str | None
    requires: dict[str, bool]
    sources: list[Path]
    include_dirs: list[Path]
    defines: list[str]
    linker_script: Path | None


def load_app(root: Path) -> App:
    path = root / 'app.toml'
    app = _get(load_toml(path), 'app', path, '', dict)
    if 'build' in app:
        raise LeanBspError(
            f'{path}: [app.build] is not supported yet; '
            'this version of lean-bsp builds applications given as [app.sources]'
        )
    sources = _get(app, 'sources', path, 'app', dict)
    files = _str_list(sources, 'files', path, 'app.sources')
    if not files:
        raise LeanBspError(f'{path}: app.sources.files is empty')
    source_paths = [(root / f).resolve() for f in files]
    missing = [str(p) for p in source_paths if not p.is_file()]
    if missing:
        raise LeanBspError(f'{path}: source file(s) not found: {", ".join(missing)}')
    linker = _get(app, 'linker', path, 'app', dict, {})
    custom_script = _get(linker, 'custom_script', path, 'app.linker', str, '')
    defines = _str_list(sources, 'defines', path, 'app.sources', [])
    for d in defines:
        _split_define(d, f'{path}: app.sources.defines')
    return App(
        root=root,
        name=_get(app, 'name', path, 'app'),
        contract_version=_get(app, 'lean_bsp_contract_version', path, 'app'),
        version=_get(app, 'version', path, 'app', str, '0.0.0'),
        default_bsp=_get(app, 'default_bsp', path, 'app', str, None),
        requires=_primitives(_get(app, 'requires', path, 'app', dict, {}), path, 'app.requires'),
        sources=source_paths,
        include_dirs=[(root / d).resolve() for d in _str_list(sources, 'include_dirs', path, 'app.sources', [])],
        defines=defines,
        linker_script=(root / custom_script).resolve() if custom_script else None,
    )


def _split_define(define: str, where: str) -> tuple[str, str | None]:
    key, sep, value = define.partition('=')
    if not key or not key.replace('_', 'a').isalnum() or key[0].isdigit():
        raise LeanBspError(f'{where}: invalid define {define!r}, expected NAME or NAME=VALUE')
    return key, value if sep else None


def merge_defines(app_defines: list[str], cli_defines: list[str]) -> list[str]:
    """app.toml's defines overridden by --define values with the same name, in a stable order."""
    merged: dict[str, str | None] = {}
    for d in app_defines:
        key, value = _split_define(d, 'app.toml')
        merged[key] = value
    for d in cli_defines:
        key, value = _split_define(d, '--define')
        merged[key] = value
    return [k if v is None else f'{k}={v}' for k, v in merged.items()]
