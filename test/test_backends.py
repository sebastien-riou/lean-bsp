"""leanbsp.backends: a board BSP's entry point made of a board description, with a fake toolchain and
a fake lean-hwbridge."""

import json
import sys
from pathlib import Path

import pytest

from leanbsp.backends import board, gcc

BSP_TOML = """\
[bsp]
name = "demo-board"
lean_bsp_contract_version = "1.0"
version = "0.1.0"
description = "a board described for leanbsp.backends"
entry_point = "bsp_cli.py"

[bsp.primitives]
uart = true

[[bsp.targets]]
name = "cortex-m0"
cpu = "cortex-m0"
toolchain = "fake-none-eabi"
cflags = ["-mcpu=cortex-m0", "-mthumb"]
run_mode = "auto-after-load"
"""

# A toolchain that records its command lines and writes the files it is asked for.
FAKE_TOOL = """\
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_TOOL_LOG'], 'a') as log:
    log.write(json.dumps([Path(sys.argv[0]).name, *args]) + '\\n')
if '-o' in args:
    Path(args[args.index('-o') + 1]).write_text('object')
elif len(args) >= 3 and args[0] == '-O':
    Path(args[2]).write_text(args[1])
if any(a.endswith('broken.c') for a in args):
    sys.exit(1)
"""

# lean-hwbridge as bsp_cli.py sees it: --json answers, and a console for serial cat.
FAKE_HWBRIDGE = """\
import json, os, sys
args = sys.argv[1:]
with open(os.environ['FAKE_TOOL_LOG'], 'a') as log:
    log.write(json.dumps(['hwbridge', *args]) + '\\n')
if args[:2] == ['serial', 'open']:
    print('{}')
elif args[0] in ('flash', 'reset'):
    print(json.dumps({'generation': 5, 'offset': 7}))
elif args[:2] == ['serial', 'cat']:
    print('hello from the board')
"""


@pytest.fixture
def bench(tmp_path, monkeypatch):
    root = tmp_path / 'lean-bsp-demo-board'
    (root / 'src').mkdir(parents=True)
    (root / 'bsp.toml').write_text(BSP_TOML)
    (root / 'src' / 'board.c').write_text('')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for tool in ('gcc', 'objcopy', 'size'):
        script = bindir / f'fake-none-eabi-{tool}'
        script.write_text(f'#!{sys.executable}\n{FAKE_TOOL}')
        script.chmod(0o755)
    (tmp_path / 'hwbridge.py').write_text(FAKE_HWBRIDGE)
    log = tmp_path / 'calls.jsonl'
    monkeypatch.setenv('FAKE_TOOL_LOG', str(log))
    monkeypatch.setenv('PATH', f'{bindir}:{Path(sys.executable).parent}:/usr/bin:/bin')
    monkeypatch.setenv('HWBRIDGE', f'{sys.executable} {tmp_path / "hwbridge.py"}')
    monkeypatch.delenv('CROSS_COMPILE', raising=False)
    monkeypatch.delenv('HWBRIDGE_BOARD', raising=False)
    app = {'name': 'demo', 'root': str(tmp_path), 'sources': [str(tmp_path / 'main.c')],
           'include_dirs': [str(tmp_path / 'inc')], 'defines': ['FROM_APP=1'], 'linker_script': None, 'lib': None}
    app_json = tmp_path / 'app.json'
    app_json.write_text(json.dumps(app))
    described = board.Board(root=root, title='Demo board', flash_by_hand='flashtool {hex}',
                            run_by_hand='Press RESET and read the serial port')

    def build(app, target, bdir):
        return gcc.build(app, target, bdir, root=root, sources=[root / 'src' / 'board.c'],
                         include_dirs=[root / 'src'], defines=['BOARD=1'], linker_script=root / 'board.ld',
                         link_flags=['-L', str(root)])

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return described, build, app_json, tmp_path / 'build', calls


def last_result(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_build_compiles_links_and_records(bench, capsys):
    described, build, app_json, bdir, calls = bench
    argv = ['build', '--app-json', str(app_json), '--target', 'cortex-m0', '--bdir', str(bdir)]
    assert board.main(described, build, argv) == 0
    result = last_result(capsys)
    assert result['status'] == 'ok' and result['artifact']['hex'] == str(bdir / 'demo.hex')
    compiles = [c for c in calls() if c[0] == 'fake-none-eabi-gcc' and '-c' in c]
    board_cc = next(c for c in compiles if c[-3].endswith('board.c'))
    app_cc = next(c for c in compiles if c[-3].endswith('main.c'))
    assert '-mcpu=cortex-m0' in board_cc and '-DBOARD=1' in board_cc and '-DFROM_APP=1' not in board_cc
    assert '-DBOARD=1' in app_cc and '-DFROM_APP=1' in app_cc  # the application sees the board's flags too
    link = next(c for c in calls() if c[0] == 'fake-none-eabi-gcc' and '-c' not in c)
    assert f'-T{bench[0].root / "board.ld"}' in link and '-L' in link
    assert board.read_state(bdir)['elf'] == str(bdir / 'demo.elf')


def test_build_failure_is_an_error_result(bench, capsys, tmp_path):
    described, build, app_json, bdir, _ = bench
    app = json.loads(app_json.read_text())
    app['sources'] = [str(tmp_path / 'broken.c')]
    app_json.write_text(json.dumps(app))
    argv = ['build', '--app-json', str(app_json), '--target', 'cortex-m0', '--bdir', str(bdir)]
    assert board.main(described, build, argv) == 1
    assert 'broken.c failed' in last_result(capsys)['message']


def test_cross_compile_must_match_the_target(bench, capsys, monkeypatch):
    described, build, app_json, bdir, calls = bench
    monkeypatch.setenv('CROSS_COMPILE', '/opt/riscv/bin/riscv-none-elf-')
    argv = ['build', '--app-json', str(app_json), '--target', 'cortex-m0', '--bdir', str(bdir)]
    assert board.main(described, build, argv) == 1
    assert 'needs a fake-none-eabi toolchain' in last_result(capsys)['message']
    assert calls() == []


def test_load_flashes_through_the_bridge(bench, capsys):
    described, build, app_json, bdir, calls = bench
    board.main(described, build, ['build', '--app-json', str(app_json), '--target', 'cortex-m0', '--bdir', str(bdir)])
    assert board.main(described, build, ['load', '--bdir', str(bdir)]) == 0
    assert last_result(capsys) == {'status': 'ok', 'boot': '5:7'}
    bridge = [c[1:] for c in calls() if c[0] == 'hwbridge']
    assert bridge == [['serial', 'open', 'demo-board', '--json'], ['flash', 'demo-board', str(bdir / 'demo.elf'), '--json']]


def test_load_without_bridge_says_how_to_flash(bench, capsys, monkeypatch):
    described, build, app_json, bdir, _ = bench
    board.main(described, build, ['build', '--app-json', str(app_json), '--target', 'cortex-m0', '--bdir', str(bdir)])
    monkeypatch.setenv('HWBRIDGE', 'no-such-hwbridge-command')
    assert board.main(described, build, ['load', '--bdir', str(bdir)]) == 0
    result = last_result(capsys)
    assert result['status'] == 'manual' and f'flashtool {bdir / "demo.hex"}' in result['message']


def test_run_follows_the_console(bench, capfd, monkeypatch):
    described, build, _, bdir, calls = bench
    monkeypatch.setenv('HWBRIDGE_BOARD', 'bench-nickname')
    argv = ['run', '--bdir', str(bdir), '--until', 'done', '--timeout', '30']
    assert board.main(described, build, argv) == 0
    out = capfd.readouterr().out  # the console comes straight from hwbridge's file descriptor
    assert 'hello from the board' in out
    assert json.loads(out.strip().splitlines()[-1]) == {
        'status': 'ok', 'com_device': 'hwbridge:bench-nickname', 'exit_code': None, 'boot': '5:7'}
    cat = [c[1:] for c in calls() if c[0] == 'hwbridge'][-1]
    assert cat == ['serial', 'cat', 'bench-nickname', '--from', '5:7', '--until', 'done', '--timeout', '30.0']


def test_run_detached_returns_at_once(bench, capsys):
    described, build, _, bdir, calls = bench
    assert board.main(described, build, ['run', '--bdir', str(bdir), '--detach']) == 0
    assert last_result(capsys)['boot'] == '5:7'
    assert not any(c[1:3] == ['serial', 'cat'] for c in calls())
