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
    'cycle_timer',
    'flash_read',
    'flash_erase',
    'flash_write',
)
FLASH_PRIMITIVES = ('flash_read', 'flash_erase', 'flash_write')
TRUSTZONE_STATES = ('off', 'secure', 'non-secure')

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
class Core:
    """A kind of core the board has, whether or not a target builds for it."""

    id: str
    cpu: str | None
    cflags: list[str] | None
    count: int  # how many of them the board has
    description: str


@dataclass
class NvmRegion:
    """Flash the firmware image never occupies, for the application's own data (contract 1.2)."""

    name: str
    base: int
    size: int
    page_size: int  # the erase unit
    write_size: int  # the program unit
    zero_overwrite: bool  # zeros may be programmed over a programmed unit


@dataclass
class Part:
    """The board's MCU ([bsp.mcu]) or a part outside it ([[bsp.parts]]), contract 1.5."""

    vendor: str
    part: str  # the ordering code, from the board vendor's documentation
    families: list[str]  # the vendor's names for what the part belongs to, widest first
    checked: bool  # the BSP checks at startup what the part says of itself
    role: str | None = None  # an outside part: what the board uses it for, unique within the BSP
    reserved: list[str] | None = None  # the MCU: the peripherals the BSP keeps for itself


@dataclass
class Target:
    name: str
    cpu: str
    toolchain: str
    cflags: list[str]
    linker_fragments: list[str]
    run_mode: str
    core_id: str | None  # the core the application runs on, when the BSP describes its cores
    primitives: dict[str, bool]  # effective: BSP defaults merged with this target's override
    trustzone: str | None = None  # the security state the application runs in, on an Armv8-M CPU with TrustZone


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
    cores: list[Core]
    nvm_regions: list[NvmRegion]
    targets: list[Target]
    mcu: Part | None = None
    parts: list[Part] | None = None

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

    cores: dict[str, Core] = {}
    for core in _get(bsp, 'cores', path, 'bsp', list, []):
        core_id = _get(core, 'id', path, 'bsp.cores')
        where = f'bsp.cores[{core_id}]'
        if core_id in cores:
            raise LeanBspError(f'{path}: duplicate core id {core_id!r}')
        count = _get(core, 'count', path, where, int, 1)
        if isinstance(count, bool) or count < 1:
            raise LeanBspError(f'{path}: {where}.count must be a positive integer')
        cores[core_id] = Core(
            id=core_id,
            cpu=_get(core, 'cpu', path, where, str, None),
            cflags=_str_list(core, 'cflags', path, where) if 'cflags' in core else None,
            count=count,
            description=_get(core, 'description', path, where, str, ''),
        )

    contract_version = _get(bsp, 'lean_bsp_contract_version', path, 'bsp')
    nvm_regions = _nvm_regions(bsp, path, contract_version)

    targets: list[Target] = []
    for t in _get(bsp, 'targets', path, 'bsp', list):
        tname = _get(t, 'name', path, 'bsp.targets')
        where = f'bsp.targets[{tname}]'
        if any(existing.name == tname for existing in targets):
            raise LeanBspError(f'{path}: duplicate target name {tname!r}')
        core_id = _get(t, 'core_id', path, where, str, None)
        core = None
        if core_id is not None:
            if core_id not in cores:
                raise LeanBspError(f'{path}: {where}.core_id {core_id!r} is not declared in [[bsp.cores]]')
            core = cores[core_id]
        cpu = _get(t, 'cpu', path, where, str, core.cpu if core else None)
        if cpu is None:
            raise LeanBspError(f'{path}: missing {where}.cpu')
        cflags = _str_list(t, 'cflags', path, where, core.cflags if core and core.cflags is not None else [])
        run_mode = _get(t, 'run_mode', path, where)
        if run_mode not in RUN_MODES:
            raise LeanBspError(f'{path}: {where}.run_mode must be one of {", ".join(RUN_MODES)}')
        trustzone = _get(t, 'trustzone', path, where, str, None)
        if trustzone is not None:
            if trustzone not in TRUSTZONE_STATES:
                raise LeanBspError(f'{path}: {where}.trustzone must be one of {", ".join(TRUSTZONE_STATES)}')
            if not _contract_at_least(contract_version, 1, 5):
                raise LeanBspError(f'{path}: {where}.trustzone needs contract 1.5 or later, '
                                   f'the BSP implements {contract_version}')
        override_table = _get(t, 'primitives_override', path, where, dict, {})
        override = _primitives(override_table, path, f'{where}.primitives_override')
        flash = [p for p in FLASH_PRIMITIVES if {**defaults, **override}[p]]
        if flash and not nvm_regions and _contract_at_least(contract_version, 1, 2):
            raise LeanBspError(f'{path}: {where} offers {", ".join(flash)} but [[bsp.nvm_regions]] declares no region')
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
                trustzone=trustzone,
            )
        )
    if not targets:
        raise LeanBspError(f'{path}: [[bsp.targets]] is empty')
    mcu, parts = _parts(bsp, path, contract_version, targets)

    return Bsp(
        root=root,
        name=name,
        contract_version=contract_version,
        version=_get(bsp, 'version', path, 'bsp', str, '0.0.0'),
        description=_get(bsp, 'description', path, 'bsp', str, ''),
        entry_point=root / _get(bsp, 'entry_point', path, 'bsp'),
        requires_tools=_str_list(build, 'requires_tools', path, 'bsp.build', []),
        setup_steps=_str_list(setup, 'steps', path, 'bsp.setup', []),
        primitives=defaults,
        cores=list(cores.values()),
        nvm_regions=nvm_regions,
        targets=targets,
        mcu=mcu,
        parts=parts,
    )


def _contract_at_least(version: str, major: int, minor: int) -> bool:
    try:
        have = tuple(int(part) for part in version.split('.')[:2])
    except ValueError:
        return False
    return have >= (major, minor)


def _whole(table: dict, key: str, path: Path, where: str, *, minimum: int) -> int:
    value = _get(table, key, path, where, int)
    if isinstance(value, bool) or value < minimum:
        raise LeanBspError(f'{path}: {where}.{key} must be an integer of at least {minimum}')
    return value


def _nvm_regions(bsp: dict, path: Path, contract_version: str) -> list[NvmRegion]:
    regions: list[NvmRegion] = []
    for i, table in enumerate(_get(bsp, 'nvm_regions', path, 'bsp', list, [])):
        where = f'bsp.nvm_regions[{i}]'
        if not isinstance(table, dict):
            raise LeanBspError(f'{path}: {where} must be a table')
        region = NvmRegion(
            name=_get(table, 'name', path, where),
            base=_whole(table, 'base', path, where, minimum=0),
            size=_whole(table, 'size', path, where, minimum=1),
            page_size=_whole(table, 'page_size', path, where, minimum=1),
            write_size=_whole(table, 'write_size', path, where, minimum=1),
            zero_overwrite=_get(table, 'zero_overwrite', path, where, bool, False),
        )
        if region.write_size & (region.write_size - 1):
            raise LeanBspError(f'{path}: {where}.write_size must be a power of two')
        if region.page_size % region.write_size:
            raise LeanBspError(f'{path}: {where}.page_size must be a multiple of write_size')
        if region.base % region.page_size or region.size % region.page_size:
            raise LeanBspError(f'{path}: {where}.base and .size must be multiples of page_size')
        if any(r.name == region.name for r in regions):
            raise LeanBspError(f'{path}: duplicate NVM region name {region.name!r}')
        regions.append(region)
    if regions and not _contract_at_least(contract_version, 1, 2):
        raise LeanBspError(f'{path}: [[bsp.nvm_regions]] needs contract 1.2 or later, the BSP implements {contract_version}')
    return regions


def _part(table: Any, path: Path, where: str, *, role: bool) -> Part:
    if not isinstance(table, dict):
        raise LeanBspError(f'{path}: {where} must be a table')
    known = {'vendor', 'part', 'families', 'checked', 'role' if role else 'reserved'}
    unknown = sorted(set(table) - known)
    if unknown:
        raise LeanBspError(f'{path}: unknown key(s) in {where}: {", ".join(unknown)}; '
                           f'known: {", ".join(sorted(known))}')
    part = Part(
        vendor=_get(table, 'vendor', path, where),
        part=_get(table, 'part', path, where),
        families=_str_list(table, 'families', path, where, []),
        checked=_get(table, 'checked', path, where, bool, False),
        role=_get(table, 'role', path, where) if role else None,
        reserved=None if role else _str_list(table, 'reserved', path, where, []),
    )
    for name in (part.vendor, part.part, *part.families, *([part.role] if role else [])):
        if not macro_name(name):
            raise LeanBspError(f'{path}: {where}: {name!r} has no letter or digit to name a define after')
    return part


def _parts(bsp: dict, path: Path, contract_version: str, targets: list[Target]) -> tuple[Part | None, list[Part]]:
    """[bsp.mcu] and [[bsp.parts]], contract 1.5: required of a BSP that drives a board, which every
    BSP does but one whose targets all run as a native process (linux-host)."""
    declared = 'mcu' in bsp or 'parts' in bsp
    if declared and not _contract_at_least(contract_version, 1, 5):
        raise LeanBspError(f'{path}: [bsp.mcu] and [[bsp.parts]] need contract 1.5 or later, '
                           f'the BSP implements {contract_version}')
    native = all(t.run_mode == 'native-process' for t in targets)
    if 'mcu' not in bsp:
        if _contract_at_least(contract_version, 1, 5) and not native:
            raise LeanBspError(f'{path}: missing [bsp.mcu]: a BSP at contract 1.5 or later names the MCU it drives')
        mcu = None
    else:
        mcu = _part(bsp['mcu'], path, 'bsp.mcu', role=False)
    parts: list[Part] = []
    for i, table in enumerate(_get(bsp, 'parts', path, 'bsp', list, [])):
        part = _part(table, path, f'bsp.parts[{i}]', role=True)
        if any(macro_name(p.role) == macro_name(part.role) for p in parts):
            raise LeanBspError(f'{path}: two [[bsp.parts]] have the role {part.role!r}')
        parts.append(part)
    return mcu, parts


def macro_name(name: str) -> str:
    """A bsp.toml value as part of a define's name: upper case, '_' for every character outside A-Z
    and 0-9; empty when it holds no letter or digit at all."""
    if not any(c.isascii() and c.isalnum() for c in name):
        return ''
    return ''.join(c if c.isascii() and c.isalnum() else '_' for c in name.upper())


def _c_string(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def part_defines(bsp: Bsp, target: Target) -> list[str]:
    """What the board is made of, as defines for the application's compilation (contract 1.5)."""
    defines = []
    if bsp.mcu is not None:
        defines += [f'LEAN_BSP_MCU_VENDOR={_c_string(bsp.mcu.vendor)}', f'LEAN_BSP_MCU_PART={_c_string(bsp.mcu.part)}']
        defines += [f'LEAN_BSP_MCU_IS_{n}=1' for n in _unique(macro_name(x) for x in (bsp.mcu.part, *bsp.mcu.families))]
    for part in bsp.parts or []:
        role = f'LEAN_BSP_PART_{macro_name(part.role)}'
        defines += [f'{role}={_c_string(part.part)}', f'{role}_VENDOR={_c_string(part.vendor)}']
        defines += [f'{role}_IS_{n}=1' for n in _unique(macro_name(x) for x in (part.part, *part.families))]
    if target.trustzone is not None:
        defines.append(f'LEAN_BSP_TRUSTZONE_{macro_name(target.trustzone)}=1')
    return defines


def _unique(names) -> list[str]:
    return list(dict.fromkeys(names))


def nvm_defines(bsp: Bsp) -> list[str]:
    """The geometry of the BSP's NVM regions, as defines for the application's compilation."""
    if not bsp.nvm_regions:
        return []
    defines = [f'LEAN_BSP_NVM_REGIONS={len(bsp.nvm_regions)}']
    for i, r in enumerate(bsp.nvm_regions):
        prefix = f'LEAN_BSP_NVM{i}_'
        defines += [
            f'{prefix}BASE=0x{r.base:08X}',
            f'{prefix}SIZE=0x{r.size:X}',
            f'{prefix}PAGE_SIZE={r.page_size}',
            f'{prefix}WRITE_SIZE={r.write_size}',
            f'{prefix}ZERO_OVERWRITE={int(r.zero_overwrite)}',
        ]
    return defines


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
    lib: str | None = None  # [app.build]: the archive the application builds itself, {cpu} for the target's cpu
    link_flags: list[str] | None = None  # [app.build]: what linking that archive needs
    link_flags_by_cpu: dict[str, list[str]] | None = None  # [app.build]: more of them, for one cpu

    def lib_for(self, cpu: str) -> Path | None:
        """The archive for a target's cpu, when the application builds one ([app.build])."""
        return (self.root / self.lib.replace('{cpu}', cpu)).resolve() if self.lib else None

    def link_flags_for(self, cpu: str) -> list[str]:
        return [*(self.link_flags or []), *(self.link_flags_by_cpu or {}).get(cpu, [])]


def _app_build(app: dict, path: Path) -> tuple[str | None, list[str], dict[str, list[str]]]:
    """[app.build] in its prebuilt form: an archive the application built itself, which the BSP links."""
    if 'build' not in app:
        return None, [], {}
    build = _get(app, 'build', path, 'app', dict)
    if 'command' in build:
        raise LeanBspError(f'{path}: app.build.command is not supported yet: build the archive yourself and name it '
                           'in app.build.lib')
    unknown = sorted(set(build) - {'lib', 'link_flags', 'link_flags_by_cpu'})
    if unknown:
        raise LeanBspError(f'{path}: unknown key(s) in app.build: {", ".join(unknown)}; '
                           'known: lib, link_flags, link_flags_by_cpu')
    lib = _get(build, 'lib', path, 'app.build')
    if not lib:
        raise LeanBspError(f'{path}: app.build.lib is empty')
    by_cpu = _get(build, 'link_flags_by_cpu', path, 'app.build', dict, {})
    return (lib, _str_list(build, 'link_flags', path, 'app.build', []),
            {cpu: _str_list(by_cpu, cpu, path, 'app.build.link_flags_by_cpu') for cpu in by_cpu})


def load_app(root: Path) -> App:
    path = root / 'app.toml'
    app = _get(load_toml(path), 'app', path, '', dict)
    lib, link_flags, link_flags_by_cpu = _app_build(app, path)
    if lib is None and 'sources' not in app:
        raise LeanBspError(f'{path}: missing app.sources: an application gives its sources, an archive '
                           '([app.build]), or both')
    sources = _get(app, 'sources', path, 'app', dict, {})
    files = _str_list(sources, 'files', path, 'app.sources', [])
    if not files and lib is None:
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
        lib=lib,
        link_flags=link_flags,
        link_flags_by_cpu=link_flags_by_cpu,
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
