"""Resolve which application, BSP, target and build directory a command is about, then build/load/run."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from leanbsp import __version__, adapter, compat
from leanbsp.errors import LeanBspError, info
from leanbsp.manifest import App, Bsp, Target, load_app, load_bsp, merge_defines
from leanbsp.registry import load_registry
from leanbsp.workspace import (
    existing_dir,
    find_app_root,
    list_local_bsps,
    resolve_bsp_dir,
    workspace_of_app,
    workspace_of_cwd,
)

META_DIR = '.lean-bsp'
APP_JSON = 'app.json'
BUILD_RESULT = 'build-result.json'


@dataclass
class Context:
    bsp: Bsp
    target: Target
    bdir: Path
    app: App | None = None  # None when the build was named by --bdir alone


def resolve_app(app_arg: str | None) -> App:
    if app_arg is not None:
        root = existing_dir(app_arg, '--app')
        if not (root / 'app.toml').is_file():
            raise LeanBspError(f'{root} is not an application: it has no app.toml')
        return load_app(root)
    cwd = Path.cwd()
    root = find_app_root(cwd)
    if root is None:
        raise LeanBspError(f'no app.toml in {cwd} or its parents up to the git root; pass --app <path>')
    return load_app(root)


def resolve_bsp(bsp_arg: str | None, app: App | None) -> Bsp:
    spec = bsp_arg or os.environ.get('LEAN_BSP_DEFAULT_BSP') or (app.default_bsp if app else None)
    workspace = workspace_of_app(app.root) if app else workspace_of_cwd(Path.cwd())
    if not spec:
        local = ', '.join(name for name, _ in list_local_bsps(workspace)) or 'none'
        raise LeanBspError(
            'no BSP given: pass --bsp, set LEAN_BSP_DEFAULT_BSP, or set default_bsp in app.toml; '
            f'BSPs available in {workspace}: {local}'
        )
    return load_bsp(resolve_bsp_dir(spec, workspace, load_registry()))


def default_bdir(app: App, bsp: Bsp, target: Target) -> Path:
    return app.root / 'build' / bsp.name / target.name


def context_from_app(app_arg: str | None, bsp_arg: str | None, target_arg: str | None, bdir_arg: str | None) -> Context:
    app = resolve_app(app_arg)
    bsp = resolve_bsp(bsp_arg, app)
    target = bsp.target(target_arg)
    bdir = Path(bdir_arg).expanduser().resolve() if bdir_arg else default_bdir(app, bsp, target)
    return Context(bsp=bsp, target=target, bdir=bdir, app=app)


def read_build_result(bdir: Path) -> dict | None:
    path = bdir / META_DIR / BUILD_RESULT
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def context_from_bdir(bdir_arg: str) -> Context:
    """A build named by its directory alone: build-result.json says which BSP and target made it."""
    bdir = existing_dir(bdir_arg, '--bdir')
    record = read_build_result(bdir)
    if record is None:
        raise LeanBspError(f'no build in {bdir}: {META_DIR}/{BUILD_RESULT} is missing')
    bsp_root = Path(record['bsp']['path'])
    if not (bsp_root / 'bsp.toml').is_file():
        # the BSP moved since the build: find it again by name, next to the recorded application
        workspace = workspace_of_app(Path(record['app']['path']))
        bsp_root = resolve_bsp_dir(record['bsp']['name'], workspace, load_registry())
    bsp = load_bsp(bsp_root)
    return Context(bsp=bsp, target=bsp.target(record['target']), bdir=bdir)


def context_for_existing_build(
    app_arg: str | None, bsp_arg: str | None, target_arg: str | None, bdir_arg: str | None
) -> Context:
    """load/run/load-run: --bdir alone names a build directly, anything else re-derives it from app + BSP."""
    if bdir_arg and app_arg is None and bsp_arg is None and target_arg is None:
        return context_from_bdir(bdir_arg)
    return context_from_app(app_arg, bsp_arg, target_arg, bdir_arg)


def check(ctx: Context) -> list[str]:
    assert ctx.app is not None
    return compat.problems(ctx.app, ctx.bsp, ctx.target)


def build(ctx: Context, cli_defines: list[str]) -> dict:
    app = ctx.app
    assert app is not None
    compat.check(app, ctx.bsp, ctx.target)
    meta = ctx.bdir / META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    (meta / BUILD_RESULT).unlink(missing_ok=True)  # a failed build must not leave the previous result behind
    defines = merge_defines(app.defines, cli_defines)
    app_json = {
        'name': app.name,
        'root': str(app.root),
        'sources': [str(p) for p in app.sources],
        'include_dirs': [str(p) for p in app.include_dirs],
        'defines': defines,
        'linker_script': str(app.linker_script) if app.linker_script else None,
        'lib': None,
    }
    app_json_path = meta / APP_JSON
    app_json_path.write_text(json.dumps(app_json, indent=2) + '\n', encoding='utf-8')
    result = adapter.invoke(
        ctx.bsp,
        'build',
        ['--app-json', str(app_json_path), '--target', ctx.target.name, '--bdir', str(ctx.bdir)],
    )
    if result['status'] == 'ok':
        record = {
            'artifact': result.get('artifact', {}),
            'bsp': {'name': ctx.bsp.name, 'path': str(ctx.bsp.root), 'version': ctx.bsp.version},
            'target': ctx.target.name,
            'app': {'name': app.name, 'path': str(app.root), 'version': app.version},
            'defines': defines,
            'contract_version': ctx.bsp.contract_version,
            'lean_bsp_version': __version__,
        }
        (meta / BUILD_RESULT).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        info(f'built {app.name} for {ctx.bsp.name}/{ctx.target.name} in {ctx.bdir}')
    return result


def require_build(ctx: Context) -> None:
    if not ctx.bdir.exists():
        raise LeanBspError(f'{ctx.bdir} does not exist; run lbsp build first')
    if read_build_result(ctx.bdir) is None:
        raise LeanBspError(f'no build in {ctx.bdir}; run lbsp build first')


def load(ctx: Context) -> dict:
    require_build(ctx)
    return adapter.invoke(ctx.bsp, 'load', ['--bdir', str(ctx.bdir), '--target', ctx.target.name])


def run(ctx: Context, *, detach: bool, timeout: str | None, until: str | None = None) -> dict:
    require_build(ctx)
    args = ['--bdir', str(ctx.bdir), '--target', ctx.target.name]
    if detach:
        args.append('--detach')
    if timeout is not None:
        args += ['--timeout', timeout]
    if until is not None:
        args += ['--until', until]
    return adapter.invoke(ctx.bsp, 'run', args, interactive=not detach)
