"""lean-bsp / lbsp command line."""

import argparse
import json
import shutil
import sys
from pathlib import Path

from leanbsp import __version__, adapter, compat, flow, toolchain
from leanbsp.errors import LeanBspError, info
from leanbsp.fetch import fetch
from leanbsp.manifest import load_bsp
from leanbsp.registry import extra_registry_files, load_registry
from leanbsp.workspace import (
    bsp_dir,
    find_app_root,
    list_local_bsps,
    workspace_of_app,
    workspace_of_cwd,
)

EXIT_ERROR = 1
EXIT_MANUAL = 3

EPILOG = """\
exit status: 0 on success, 1 on error, 2 on a usage error, 3 when a BSP reports that a step
needs a human. When a run reports the application's exit code, a non-zero one becomes lean-bsp's
own exit status (128 + signal number for an application killed by a signal).
"""


def current_workspace() -> Path:
    cwd = Path.cwd()
    app_root = find_app_root(cwd)
    return workspace_of_app(app_root) if app_root else workspace_of_cwd(cwd)


def status_code(result: dict) -> int:
    if result['status'] == 'manual':
        info('manual step: ' + (result.get('message') or 'this step needs a human, see the output above'))
        return EXIT_MANUAL
    if result['status'] == 'error':
        raise LeanBspError(result.get('message') or 'the BSP reported an error, see the output above')
    return 0


def run_code(result: dict) -> int:
    code = status_code(result)
    exit_code = result.get('exit_code')
    if code or not isinstance(exit_code, int) or exit_code == 0:
        return code
    if exit_code < 0:
        return 128 - exit_code
    return exit_code if exit_code <= 255 else EXIT_ERROR


# ---- commands ----


def cmd_list_bsps(args) -> int:
    registry = load_registry()
    workspace = current_workspace()
    local = dict(list_local_bsps(workspace))
    rows = []
    for name in sorted(set(registry) | set(local)):
        entry = registry.get(name)
        if args.tag and (entry is None or args.tag not in entry.tags):
            continue
        path = bsp_dir(workspace, name)
        rows.append(
            {
                'name': name,
                'cloned': str(path) if path.is_dir() else None,
                'ready': name in local,  # cloned and has a bsp.toml
                'description': entry.description if entry else '',
                'tags': entry.tags if entry else [],
                'git': entry.git if entry else None,
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    width = max((len(r['name']) for r in rows), default=4)
    for r in rows:
        state = 'ready' if r['ready'] else 'no bsp.toml' if r['cloned'] else 'not cloned'
        print(f'{r["name"]:<{width}}  {state:<11}  {r["description"]}')
    return 0


def cmd_show_bsp(args) -> int:
    registry = load_registry()
    entry = registry.get(args.name)
    path = bsp_dir(current_workspace(), args.name)
    if entry is None and not (path / 'bsp.toml').is_file():
        raise LeanBspError(f'unknown BSP {args.name!r}')
    print(f'name:        {args.name}')
    if entry:
        print(f'git:         {entry.git}')
        print(f'description: {entry.description}')
        print(f'tags:        {", ".join(entry.tags)}')
        if entry.default_ref:
            print(f'default ref: {entry.default_ref}')
        print(f'registry:    {entry.source}')
    if not (path / 'bsp.toml').is_file():
        if path.is_dir():
            print(f'cloned:      {path}, but it has no bsp.toml yet')
        else:
            print(f'cloned:      no (lbsp fetch {args.name} clones it into {path})')
        return 0
    bsp = load_bsp(path)
    print(f'cloned:      {path}')
    print(f'version:     {bsp.version}')
    print(f'contract:    {bsp.contract_version}')
    if bsp.mcu:
        print(f'mcu:         {_part_line(bsp.mcu)}')
        print(f'reserved:    {", ".join(bsp.mcu.reserved) or "nothing"}')
    for p in bsp.parts or []:
        print(f'part:        {p.role}: {_part_line(p)}')
    for c in bsp.cores:
        print(f'core:        {_core_line(c)}')
    for r in bsp.nvm_regions:
        print(f'nvm region:  {_nvm_line(r)}')
    for t in bsp.targets:
        primitives = ', '.join(k for k, v in t.primitives.items() if v)
        print(f'target:      {t.name} ({_on_core(t)}cpu {t.cpu}, {t.toolchain}, {t.run_mode}{_trustzone(t)}): '
              f'{primitives}')
    return 0


def _part_line(part) -> str:
    """The MCU or an outside part: `ST STM32U5A5ZJT6Q (STM32, STM32U5, STM32U5A5), not checked at startup`."""
    families = f' ({", ".join(part.families)})' if part.families else ''
    checked = 'checked' if part.checked else 'not checked'
    return f'{part.vendor} {part.part}{families}, {checked} at startup'


def _trustzone(target) -> str:
    return f', TrustZone {target.trustzone}' if target.trustzone else ''


def _part_json(part) -> dict:
    found = {'vendor': part.vendor, 'part': part.part, 'families': part.families, 'checked': part.checked}
    if part.role is not None:
        found = {'role': part.role, **found}
    if part.reserved is not None:
        found['reserved'] = part.reserved
    return found


def _target_json(target) -> dict:
    tools = toolchain.tools(target)
    return {
        'name': target.name,
        'core_id': target.core_id,
        'cpu': target.cpu,
        'toolchain': target.toolchain,
        'cflags': target.cflags,
        'run_mode': target.run_mode,
        'trustzone': target.trustzone,
        'cc': tools.cc,
        'ar': tools.ar,
        'toolchain_problem': tools.problem,
        'primitives': target.primitives,
    }


def _nvm_line(region) -> str:
    """One NVM region: `bank2 at 0x08200000, 2048 KiB, pages of 8192 bytes, writes of 16, zero overwrite`."""
    size = f'{region.size // 1024} KiB' if region.size % 1024 == 0 else f'{region.size} bytes'
    overwrite = ', zero overwrite' if region.zero_overwrite else ''
    return (f'{region.name} at 0x{region.base:08X}, {size}, pages of {region.page_size} bytes, '
            f'writes of {region.write_size}{overwrite}')


def _core_line(core) -> str:
    """One of the board's kinds of core: `u54 x4 (rv64gc): application harts`."""
    count = f' x{core.count}' if core.count > 1 else ''
    cpu = f' ({core.cpu})' if core.cpu else ''
    return f'{core.id}{count}{cpu}' + (f': {core.description}' if core.description else '')


def _on_core(target) -> str:
    return f'on core {target.core_id}, ' if target.core_id else ''


def cmd_fetch(args) -> int:
    registry = load_registry()
    entry = registry.get(args.name)
    if entry is None:
        raise LeanBspError(f'unknown BSP {args.name!r}; known: {", ".join(sorted(registry))}')
    dest = Path(args.dir).expanduser().resolve() if args.dir else bsp_dir(current_workspace(), args.name)
    fetch(entry, dest, args.ref)
    info(f'BSP {args.name} ready in {dest}')
    return 0


def cmd_registry_list(args) -> int:
    print('built-in')
    for path in extra_registry_files():
        print(f'{path}{"" if path.is_file() else " (missing)"}')
    return 0


def cmd_doctor(args) -> int:
    problems = []
    if sys.version_info < (3, 11):
        problems.append(f'Python {sys.version.split()[0]} is too old, lean-bsp needs 3.11 or later')
    if shutil.which('git') is None:
        problems.append('git is not installed')
    if args.bsp:
        bsps = [flow.resolve_bsp(args.bsp, None)]
    else:
        bsps = []
        for name, path in list_local_bsps(current_workspace()):
            try:
                bsps.append(load_bsp(path))
            except LeanBspError as e:
                problems.append(f'BSP {name}: {e}')
    for bsp in bsps:
        found = compat.bsp_problems(bsp)
        missing_tools = [tool for tool in bsp.requires_tools if not shutil.which(tool)]
        found += [f'BSP {bsp.name} needs {tool}, which is not on PATH' for tool in missing_tools]
        problems += found
        print(f'{bsp.name}: {"ok" if not found else "problems found"} ({bsp.root})')
    for p in problems:
        print(f'  {p}')
    return EXIT_ERROR if problems else 0


def cmd_check(args) -> int:
    ctx = flow.context_from_app(args.app, args.bsp, args.target, None)
    problems = flow.check(ctx)
    for p in problems:
        print(p)
    if problems:
        return EXIT_ERROR
    assert ctx.app is not None
    print(f'{ctx.app.name} is compatible with {ctx.bsp.name}/{ctx.target.name}')
    return 0


def cmd_build(args) -> int:
    ctx = flow.context_from_app(args.app, args.bsp, args.target, args.bdir)
    return status_code(flow.build(ctx, args.define))


def cmd_load(args) -> int:
    ctx = flow.context_for_existing_build(args.app, args.bsp, args.target, args.bdir)
    return status_code(flow.load(ctx))


def cmd_run(args) -> int:
    ctx = flow.context_for_existing_build(args.app, args.bsp, args.target, args.bdir)
    return run_code(flow.run(ctx, detach=args.detach, timeout=args.timeout, until=args.until))


def cmd_build_load_run(args) -> int:
    ctx = flow.context_from_app(args.app, args.bsp, args.target, args.bdir)
    code = status_code(flow.build(ctx, args.define))
    if code:
        return code
    code = status_code(flow.load(ctx))
    if code:
        return code
    return run_code(flow.run(ctx, detach=args.detach, timeout=args.timeout, until=args.until))


def cmd_load_run(args) -> int:
    ctx = flow.context_for_existing_build(args.app, args.bsp, args.target, args.bdir)
    if flow.read_build_result(ctx.bdir) is None:
        if ctx.app is None:
            raise LeanBspError(f'no build in {ctx.bdir}')
        info(f'no build in {ctx.bdir} yet, building first')
        code = status_code(flow.build(ctx, []))
        if code:
            return code
    code = status_code(flow.load(ctx))
    if code:
        return code
    return run_code(flow.run(ctx, detach=args.detach, timeout=args.timeout, until=args.until))


def cmd_capabilities(args) -> int:
    app = None
    if not args.bsp:
        app_root = find_app_root(Path.cwd())
        app = flow.resolve_app(str(app_root)) if app_root else None
    bsp = flow.resolve_bsp(args.bsp, app)
    dynamic = adapter.invoke(bsp, 'capabilities', ['--json'], echo=False)
    merged = {
        'name': bsp.name,
        'version': bsp.version,
        'contract_version': bsp.contract_version,
        'description': bsp.description,
        'path': str(bsp.root),
        'cores': [
            {'id': c.id, 'cpu': c.cpu, 'cflags': c.cflags, 'count': c.count, 'description': c.description}
            for c in bsp.cores
        ],
        'mcu': _part_json(bsp.mcu) if bsp.mcu else None,
        'parts': [_part_json(p) for p in bsp.parts or []],
        'nvm_regions': [
            {'name': r.name, 'base': r.base, 'size': r.size, 'page_size': r.page_size, 'write_size': r.write_size,
             'zero_overwrite': r.zero_overwrite}
            for r in bsp.nvm_regions
        ],
        'targets': [_target_json(t) for t in bsp.targets],
        'dynamic': {k: v for k, v in dynamic.items() if k != 'status'},
    }
    if args.json:
        print(json.dumps(merged, indent=2))
        return 0
    print(f'{bsp.name} {bsp.version} (contract {bsp.contract_version}): {bsp.description}')
    if bsp.mcu:
        print(f'  mcu {_part_line(bsp.mcu)}; reserved: {", ".join(bsp.mcu.reserved) or "nothing"}')
    for p in bsp.parts or []:
        print(f'  part {p.role}: {_part_line(p)}')
    for c in bsp.cores:
        print(f'  core {_core_line(c)}')
    for r in bsp.nvm_regions:
        print(f'  nvm region {_nvm_line(r)}')
    for t in bsp.targets:
        supported = ', '.join(k for k, v in t.primitives.items() if v) or 'none'
        tools = toolchain.tools(t)
        compiler = tools.cc or f'no compiler ({tools.problem})'
        print(f'  target {t.name}: {_on_core(t)}cpu {t.cpu}, {t.toolchain}, {t.run_mode}{_trustzone(t)}; '
              f'primitives: {supported}; compiler: {compiler}')
    for k, v in merged['dynamic'].items():
        print(f'  {k}: {v}')
    return 0


# ---- argument parsing ----


def _app_bsp_target(p: argparse.ArgumentParser) -> None:
    p.add_argument('--app', help='application directory (default: the one enclosing the current directory)')
    p.add_argument('--bsp', help='BSP name or path (default: LEAN_BSP_DEFAULT_BSP, then app.toml default_bsp)')
    p.add_argument('--target', help='BSP target (default: the only one, when the BSP has a single target)')


def _bdir(p: argparse.ArgumentParser) -> None:
    p.add_argument('--bdir', help='build directory (default: <app>/build/<bsp>/<target>)')


def _define(p: argparse.ArgumentParser) -> None:
    p.add_argument('--define', action='append', default=[], metavar='K=V', help='extra define, repeatable')


def _run_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        '--detach',
        action='store_true',
        help='return as soon as the application runs (native processes and emulators wait for it by default)',
    )
    p.add_argument(
        '--timeout',
        metavar='S',
        help='bound the wait for the application to exit, in seconds (default: no bound; -1 also means none)',
    )
    p.add_argument(
        '--until',
        metavar='TEXT',
        help="on a board, stop following the console once TEXT was printed (it fails if --timeout comes first)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='lean-bsp',
        description='Build, load and run applications against generic, reusable BSPs.',
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version', action='version', version=f'lean-bsp {__version__}')
    sub = parser.add_subparsers(dest='command', required=True, metavar='command')

    p = sub.add_parser('list-bsps', help='list known and locally cloned BSPs')
    p.add_argument('--tag', help='only BSPs with this registry tag')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_list_bsps)

    p = sub.add_parser('show-bsp', help='show a BSP from the registry and, if cloned, its manifest')
    p.add_argument('name')
    p.set_defaults(func=cmd_show_bsp)

    p = sub.add_parser('fetch', help='clone a BSP next to the current repository and run its setup steps')
    p.add_argument('name')
    p.add_argument('--ref', help='tag, branch or commit (default: the registry default_ref, else the default branch)')
    p.add_argument('--dir', help='clone here instead of <workspace>/lean-bsp-<name>')
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser('registry', help='registry files')
    rsub = p.add_subparsers(dest='registry_command', required=True, metavar='command')
    r = rsub.add_parser('list', help='list the registry files in use, in merge order')
    r.set_defaults(func=cmd_registry_list)

    p = sub.add_parser('doctor', help='check Python, git, and the BSPs and their tools')
    p.add_argument('--bsp', help='only this BSP (default: every BSP cloned in the workspace)')
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser('check', help='check that an application and a BSP target are compatible')
    _app_bsp_target(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser('build', help='build an application for a BSP target')
    _app_bsp_target(p)
    _bdir(p)
    _define(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser('load', help='load an existing build onto its target')
    _bdir(p)
    _app_bsp_target(p)
    p.set_defaults(func=cmd_load)

    p = sub.add_parser('run', help='run an existing, loaded build')
    _bdir(p)
    _app_bsp_target(p)
    _run_options(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('build-load-run', help='build, load and run, always building')
    _app_bsp_target(p)
    _bdir(p)
    _define(p)
    _run_options(p)
    p.set_defaults(func=cmd_build_load_run)

    p = sub.add_parser('load-run', help='load and run an existing build, building only if there is none')
    _bdir(p)
    _app_bsp_target(p)
    _run_options(p)
    p.set_defaults(func=cmd_load_run)

    p = sub.add_parser('capabilities', help="show a BSP's targets and primitives")
    p.add_argument('--bsp', help='BSP name or path (default: the enclosing application default BSP)')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_capabilities)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LeanBspError as e:
        print(f'lean-bsp: error: {e}', file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
