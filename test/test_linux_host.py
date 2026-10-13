"""End to end against the real sibling repositories, when they are cloned and gcc is available."""

import shutil
from pathlib import Path

import pytest

from leanbsp.cli import main

REPOS = Path(__file__).resolve().parents[2]
LINUX_HOST = REPOS / 'lean-bsp-linux-host'
HELLO = REPOS / 'lean-app-hello-world'

pytestmark = pytest.mark.skipif(
    not (LINUX_HOST / 'bsp.toml').is_file() or not (HELLO / 'app.toml').is_file() or not shutil.which('gcc'),
    reason='needs ../lean-bsp-linux-host, ../lean-app-hello-world and gcc',
)


def test_hello_world_report(tmp_path, monkeypatch, capfd):
    monkeypatch.delenv('LEAN_BSP_DEFAULT_BSP', raising=False)
    argv = ['build-load-run', '--app', str(HELLO), '--bsp', str(LINUX_HOST), '--bdir', str(tmp_path)]
    assert main([*argv, '--define', 'HELLO_RESET_WAIT_MS=0']) == 0
    out = capfd.readouterr().out
    for line in ('hello world', 'board: linux-host', 'uart: this line was sent with bsp_uart_tx', 'hello-world done'):
        assert line in out
    assert 'heap: with 1000 bytes allocated, bsp_heap_usage() = 1000 bytes' in out
    assert out.splitlines()[-1] == '{"status": "ok", "com_device": null, "exit_code": 0}'
