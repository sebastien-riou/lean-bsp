"""Where applications and BSPs live: app.toml discovery and sibling-directory resolution."""

from pathlib import Path

from leanbsp.errors import LeanBspError
from leanbsp.registry import RegistryEntry

BSP_DIR_PREFIX = 'lean-bsp-'


def existing_dir(arg: str, option: str) -> Path:
    """The directory a command-line option names, or an error saying plainly that it is missing."""
    path = Path(arg).expanduser().resolve()
    if not path.exists():
        raise LeanBspError(f'{option} {arg}: {path} does not exist')
    if not path.is_dir():
        raise LeanBspError(f'{option} {arg}: {path} is not a directory')
    return path


def git_root(start: Path) -> Path | None:
    for d in [start, *start.parents]:
        if (d / '.git').exists():
            return d
    return None


def find_app_root(start: Path) -> Path | None:
    """The nearest directory holding app.toml, searching `start` then its ancestors up to the git root."""
    stop = git_root(start)
    for d in [start, *start.parents]:
        if (d / 'app.toml').is_file():
            return d
        if d == stop:
            break
    return None


def workspace_of_app(app_root: Path) -> Path:
    """The directory holding the application's repository, where its sibling BSPs live."""
    return (git_root(app_root) or app_root).parent


def workspace_of_cwd(cwd: Path) -> Path:
    root = git_root(cwd)
    return root.parent if root else cwd


def bsp_dir(workspace: Path, name: str) -> Path:
    return workspace / f'{BSP_DIR_PREFIX}{name}'


def list_local_bsps(workspace: Path) -> list[tuple[str, Path]]:
    return [
        (d.name[len(BSP_DIR_PREFIX) :], d)
        for d in sorted(workspace.glob(f'{BSP_DIR_PREFIX}*'))
        if (d / 'bsp.toml').is_file()
    ]


def is_path_spec(spec: str) -> bool:
    return '/' in spec or spec in ('.', '..')


def resolve_bsp_dir(spec: str, workspace: Path, registry: dict[str, RegistryEntry]) -> Path:
    """A --bsp value: a path if it contains '/', else a name resolved to <workspace>/lean-bsp-<name>."""
    if is_path_spec(spec):
        path = existing_dir(spec, '--bsp')
        if not (path / 'bsp.toml').is_file():
            raise LeanBspError(f'{path} is not a BSP: it has no bsp.toml')
        return path
    path = bsp_dir(workspace, spec)
    if (path / 'bsp.toml').is_file():
        return path
    if path.is_dir():
        raise LeanBspError(f'{path} is not a BSP yet: it has no bsp.toml')
    if spec in registry:
        raise LeanBspError(f'BSP {spec!r} is not cloned in {workspace}; run: lbsp fetch {spec}')
    local = ', '.join(name for name, _ in list_local_bsps(workspace)) or 'none'
    raise LeanBspError(f'unknown BSP {spec!r}; BSPs available in {workspace}: {local}')
