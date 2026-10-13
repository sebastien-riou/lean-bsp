import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leanbsp.compat import canonical_header  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / 'fixtures'

APP_TOML = """\
[app]
name = "demo"
lean_bsp_contract_version = "1.0"
default_bsp = "fake"

[app.requires]
uart = true

[app.sources]
files = ["main.c"]
include_dirs = ["inc"]
defines = ["FROM_APP=1", "OVERRIDDEN=app"]
"""

REGISTRY = """\
[[bsp]]
name = "fake"
git = "https://example.invalid/lean-bsp-fake.git"
description = "the test double"
tags = ["test"]

[[bsp]]
name = "elsewhere"
git = "https://example.invalid/lean-bsp-elsewhere.git"
"""


@dataclass
class Workspace:
    root: Path
    app: Path
    bsp: Path


def make_fake_bsp(dest: Path) -> Path:
    shutil.copytree(FIXTURES / 'fake-bsp', dest)
    (dest / 'include').mkdir()
    (dest / 'include' / 'bsp_api.h').write_bytes(canonical_header('1.0'))
    return dest


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Workspace:
    """<tmp>/ws holding an application repository and a cloned fake BSP, with the app as cwd."""
    root = tmp_path / 'ws'
    app = root / 'lean-app-demo'
    (app / '.git').mkdir(parents=True)
    (app / 'inc').mkdir()
    (app / 'main.c').write_text('int app_main(void) { return 0; }\n')
    (app / 'app.toml').write_text(APP_TOML)
    bsp = make_fake_bsp(root / 'lean-bsp-fake')
    registry = tmp_path / 'registry.toml'
    registry.write_text(REGISTRY)
    monkeypatch.setenv('LEAN_BSP_REGISTRY', str(registry))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv('LEAN_BSP_DEFAULT_BSP', raising=False)
    monkeypatch.chdir(app)
    return Workspace(root=root, app=app, bsp=bsp)
