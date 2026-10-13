import pytest

from leanbsp.errors import LeanBspError
from leanbsp.manifest import load_app, load_bsp, merge_defines

BSP_HEAD = """\
[bsp]
name = "board"
lean_bsp_contract_version = "1.0"
entry_point = "bsp_cli.py"

[bsp.primitives]
uart = true
flash_write = false
"""


def write_bsp(tmp_path, targets: str):
    (tmp_path / 'bsp.toml').write_text(BSP_HEAD + targets)
    return tmp_path


def test_fake_bsp_fixture(workspace):
    bsp = load_bsp(workspace.bsp)
    assert bsp.name == 'fake'
    assert bsp.version == '1.2.3'
    target = bsp.target(None)
    assert (target.name, target.cpu, target.cflags) == ('only', 'fake-cpu', ['-mfake'])
    assert target.primitives['uart'] and target.primitives['cpu_cycle_counter']
    assert not target.primitives['led']  # missing keys default to false


def test_per_target_override_and_cores(tmp_path):
    bsp = load_bsp(
        write_bsp(
            tmp_path,
            """
[[bsp.cores]]
id = "core0"
cpu = "cortex-m33"
cflags = ["-mcpu=cortex-m33"]

[[bsp.targets]]
name = "nor"
core_id = "core0"
toolchain = "arm-none-eabi"
run_mode = "auto-after-load"

[[bsp.targets]]
name = "qspi"
core_id = "core0"
toolchain = "arm-none-eabi"
run_mode = "auto-after-load"
[bsp.targets.primitives_override]
flash_write = true
""",
        )
    )
    nor, qspi = bsp.target('nor'), bsp.target('qspi')
    assert nor.cpu == qspi.cpu == 'cortex-m33'
    assert nor.cflags == ['-mcpu=cortex-m33']
    assert not nor.primitives['flash_write'] and qspi.primitives['flash_write']
    with pytest.raises(LeanBspError, match='several targets'):
        bsp.target(None)
    with pytest.raises(LeanBspError, match="no target 'sram'"):
        bsp.target('sram')


@pytest.mark.parametrize(
    ('targets', 'message'),
    [
        ('', 'missing bsp.targets'),
        ('[[bsp.targets]]\nname = "a"\ntoolchain = "gcc"\nrun_mode = "manual"\n', 'missing bsp.targets\\[a\\].cpu'),
        ('[[bsp.targets]]\nname = "a"\ncpu = "x"\ntoolchain = "gcc"\nrun_mode = "teleport"\n', 'run_mode must be'),
        (
            '[[bsp.targets]]\nname = "a"\ncpu = "x"\ntoolchain = "gcc"\nrun_mode = "manual"\n'
            '[bsp.targets.primitives_override]\nwarp_drive = true\n',
            'unknown primitive',
        ),
        (
            '[[bsp.targets]]\nname = "a"\ncpu = "x"\ntoolchain = "gcc"\nrun_mode = "manual"\n' * 2,
            'duplicate target',
        ),
    ],
)
def test_invalid_bsp(tmp_path, targets, message):
    with pytest.raises(LeanBspError, match=message):
        load_bsp(write_bsp(tmp_path, targets))


def test_app_paths_are_absolute(workspace):
    app = load_app(workspace.app)
    assert app.sources == [workspace.app / 'main.c']
    assert app.include_dirs == [workspace.app / 'inc']
    assert app.default_bsp == 'fake'


def test_app_missing_source(workspace):
    (workspace.app / 'main.c').unlink()
    with pytest.raises(LeanBspError, match='source file'):
        load_app(workspace.app)


def test_app_build_not_supported_yet(workspace):
    toml = workspace.app / 'app.toml'
    toml.write_text(toml.read_text() + '\n[app.build]\ncommand = ["./build"]\n')
    with pytest.raises(LeanBspError, match=r'\[app.build\] is not supported yet'):
        load_app(workspace.app)


def test_merge_defines():
    assert merge_defines(['A=1', 'B', 'C=app'], ['C=cli', 'D=4']) == ['A=1', 'B', 'C=cli', 'D=4']
    with pytest.raises(LeanBspError, match='invalid define'):
        merge_defines([], ['=1'])
    with pytest.raises(LeanBspError, match='invalid define'):
        merge_defines(['1ABC'], [])
