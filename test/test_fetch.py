"""fetch against a local git repository standing in for a remote: no network needed."""

import shutil
import subprocess

import pytest

from conftest import make_fake_bsp
from leanbsp.cli import main

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='needs git')

GIT_ID = ['-c', 'user.name=test', '-c', 'user.email=test@example.invalid']


def git(*args, cwd):
    subprocess.run(['git', *GIT_ID, *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def remote(workspace, tmp_path, monkeypatch):
    """A git repository holding a BSP with one setup step, registered under the name 'remote'."""
    src = make_fake_bsp(tmp_path / 'remote-src')
    toml = src / 'bsp.toml'
    toml.write_text(toml.read_text().replace('name = "fake"', 'name = "remote"') + '\n[bsp.setup]\nsteps = ["touch set-up"]\n')
    git('init', '-q', '-b', 'main', cwd=src)
    git('add', '.', cwd=src)
    git('commit', '-q', '-m', 'v1', cwd=src)
    git('tag', 'v1', cwd=src)
    (src / 'later').write_text('after v1\n')
    git('add', '.', cwd=src)
    git('commit', '-q', '-m', 'after v1', cwd=src)
    registry = tmp_path / 'remote-registry.toml'
    registry.write_text(f'[[bsp]]\nname = "remote"\ngit = "{src}"\ndefault_ref = "v1"\n')
    monkeypatch.setenv('LEAN_BSP_REGISTRY', str(registry))
    return src


def test_fetch_clones_default_ref_and_runs_setup(workspace, remote):
    assert main(['fetch', 'remote']) == 0
    dest = workspace.root / 'lean-bsp-remote'
    assert (dest / 'set-up').is_file()
    assert not (dest / 'later').exists()  # default_ref v1, not the branch tip


def test_fetch_again_reruns_setup_and_warns(workspace, remote, capfd):
    assert main(['fetch', 'remote']) == 0
    dest = workspace.root / 'lean-bsp-remote'
    (dest / 'set-up').unlink()
    (dest / 'local-change').write_text('x')
    assert main(['fetch', 'remote', '--ref', 'main']) == 0
    err = capfd.readouterr().err
    assert 'skipping the clone' in err
    assert 'has local changes' in err
    assert 'is not at main' in err
    assert (dest / 'set-up').is_file()  # setup steps run again: they are idempotent by contract


def test_fetch_into_dir(workspace, remote, tmp_path):
    dest = tmp_path / 'custom'
    assert main(['fetch', 'remote', '--dir', str(dest), '--ref', 'main']) == 0
    assert (dest / 'later').is_file()
