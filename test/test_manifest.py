import pytest

from leanbsp.errors import LeanBspError
from leanbsp.manifest import load_app, load_bsp, macro_name, merge_defines, nvm_defines, part_defines

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


MULTI_CORE = """
[[bsp.cores]]
id = "e51"
cpu = "rv64imac"
description = "monitor hart"

[[bsp.cores]]
id = "u54"
cpu = "rv64gc"
cflags = ["-march=rv64gc", "-mabi=lp64d"]
count = 4

[[bsp.targets]]
name = "e51"
core_id = "e51"
toolchain = "riscv-none-elf"
cflags = ["-march=rv64imac", "-mabi=lp64"]
run_mode = "auto-after-load"

[[bsp.targets]]
name = "u54"
core_id = "u54"
toolchain = "riscv-none-elf"
run_mode = "auto-after-load"
"""


def test_cores_are_kept_as_the_board_description(tmp_path):
    bsp = load_bsp(write_bsp(tmp_path, MULTI_CORE))
    e51, u54 = bsp.cores
    assert (e51.id, e51.cpu, e51.cflags, e51.count, e51.description) == ('e51', 'rv64imac', None, 1, 'monitor hart')
    assert (u54.id, u54.count) == ('u54', 4)
    # a core without cflags gives its targets none; the target's own are used
    assert bsp.target('e51').cflags == ['-march=rv64imac', '-mabi=lp64'] and bsp.target('e51').core_id == 'e51'
    assert bsp.target('u54').cflags == ['-march=rv64gc', '-mabi=lp64d'] and bsp.target('u54').cpu == 'rv64gc'


@pytest.mark.parametrize(
    ('cores', 'message'),
    [
        ('[[bsp.cores]]\nid = "a"\n[[bsp.cores]]\nid = "a"\n', "duplicate core id 'a'"),
        ('[[bsp.cores]]\nid = "a"\ncount = 0\n', 'count must be a positive integer'),
        ('[[bsp.cores]]\nid = "a"\ncount = true\n', 'count must be a'),
        ('[[bsp.cores]]\nid = "a"\ncflags = "-O2"\n', 'cflags must be a list'),
    ],
)
def test_invalid_cores(tmp_path, cores, message):
    target = '[[bsp.targets]]\nname = "t"\ncpu = "x"\ntoolchain = "gcc"\nrun_mode = "manual"\n'
    with pytest.raises(LeanBspError, match=message):
        load_bsp(write_bsp(tmp_path, cores + target))


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


NVM_TARGET = '[[bsp.targets]]\nname = "t"\ncpu = "x"\ntoolchain = "gcc"\nrun_mode = "manual"\n'
BANK2 = """
[[bsp.nvm_regions]]
name = "bank2"
base = 0x08200000
size = 0x200000
page_size = 8192
write_size = 16
zero_overwrite = true
"""


def write_bsp_1_2(tmp_path, body: str):
    head = BSP_HEAD.replace('lean_bsp_contract_version = "1.0"', 'lean_bsp_contract_version = "1.2"')
    (tmp_path / 'bsp.toml').write_text(head + body)
    return tmp_path


def test_nvm_regions_and_their_defines(tmp_path):
    bsp = load_bsp(write_bsp_1_2(tmp_path, BANK2 + NVM_TARGET))
    (region,) = bsp.nvm_regions
    assert (region.name, region.base, region.size, region.page_size, region.write_size, region.zero_overwrite) == (
        'bank2', 0x08200000, 0x200000, 8192, 16, True)
    assert nvm_defines(bsp) == [
        'LEAN_BSP_NVM_REGIONS=1', 'LEAN_BSP_NVM0_BASE=0x08200000', 'LEAN_BSP_NVM0_SIZE=0x200000',
        'LEAN_BSP_NVM0_PAGE_SIZE=8192', 'LEAN_BSP_NVM0_WRITE_SIZE=16', 'LEAN_BSP_NVM0_ZERO_OVERWRITE=1',
    ]


def test_no_nvm_region_no_define(tmp_path):
    assert nvm_defines(load_bsp(write_bsp_1_2(tmp_path, NVM_TARGET))) == []


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        (BANK2.replace('write_size = 16', 'write_size = 24'), 'write_size must be a power of two'),
        (BANK2.replace('page_size = 8192', 'page_size = 8'), 'page_size must be a multiple of write_size'),
        (BANK2.replace('base = 0x08200000', 'base = 0x08200100'), 'must be multiples of page_size'),
        (BANK2.replace('size = 0x200000', 'size = 0'), 'size must be an integer of at least 1'),
        (BANK2.replace('zero_overwrite = true', 'zero_overwrite = 1'), 'zero_overwrite must be a bool'),
        (BANK2 + BANK2, "duplicate NVM region name 'bank2'"),
    ],
)
def test_invalid_nvm_regions(tmp_path, body, message):
    with pytest.raises(LeanBspError, match=message):
        load_bsp(write_bsp_1_2(tmp_path, body + NVM_TARGET))


def test_nvm_regions_need_contract_1_2(tmp_path):
    with pytest.raises(LeanBspError, match='needs contract 1.2 or later'):
        load_bsp(write_bsp(tmp_path, BANK2 + NVM_TARGET))


def test_flash_primitives_need_a_region(tmp_path):
    flash_target = NVM_TARGET + '[bsp.targets.primitives_override]\nflash_write = true\n'
    with pytest.raises(LeanBspError, match='offers flash_write but'):
        load_bsp(write_bsp_1_2(tmp_path, flash_target))
    assert load_bsp(write_bsp_1_2(tmp_path, BANK2 + flash_target)).target('t').primitives['flash_write']


def test_app_paths_are_absolute(workspace):
    app = load_app(workspace.app)
    assert app.sources == [workspace.app / 'main.c']
    assert app.include_dirs == [workspace.app / 'inc']
    assert app.default_bsp == 'fake'


def test_app_missing_source(workspace):
    (workspace.app / 'main.c').unlink()
    with pytest.raises(LeanBspError, match='source file'):
        load_app(workspace.app)


def test_app_build_command_form_not_supported_yet(workspace):
    toml = workspace.app / 'app.toml'
    toml.write_text(toml.read_text() + '\n[app.build]\ncommand = ["./build"]\n')
    with pytest.raises(LeanBspError, match=r'app.build.command is not supported yet'):
        load_app(workspace.app)


def test_app_build_names_an_archive_per_cpu(workspace):
    toml = workspace.app / 'app.toml'
    toml.write_text(toml.read_text() + '\n[app.build]\nlib = "build/{cpu}/libdemo.a"\nlink_flags = ["-flto"]\n'
                    '[app.build.link_flags_by_cpu]\nrv32imcb = ["-Wl,--no-relax-gp"]\n')
    app = load_app(workspace.app)
    assert app.lib_for('cortex-m33') == workspace.app / 'build' / 'cortex-m33' / 'libdemo.a'
    assert app.link_flags_for('cortex-m33') == ['-flto']
    assert app.link_flags_for('rv32imcb') == ['-flto', '-Wl,--no-relax-gp']
    assert [s.name for s in app.sources] == ['main.c']  # its sources still count


def test_app_build_alone_needs_no_sources(workspace):
    toml = workspace.app / 'app.toml'
    text = toml.read_text().split('[app.sources]')[0]
    toml.write_text(text + '[app.build]\nlib = "libdemo.a"\n')
    app = load_app(workspace.app)
    assert app.sources == [] and app.lib_for('x') == workspace.app / 'libdemo.a'
    toml.write_text(text)
    with pytest.raises(LeanBspError, match='missing app.sources'):
        load_app(workspace.app)


@pytest.mark.parametrize('build, message', [
    ('lib = ""', 'app.build.lib is empty'),
    ('lib = "x.a"\nflags = []', 'unknown key'),
    ('link_flags = []', 'missing app.build.lib'),
])
def test_app_build_errors(workspace, build, message):
    toml = workspace.app / 'app.toml'
    toml.write_text(toml.read_text() + f'\n[app.build]\n{build}\n')
    with pytest.raises(LeanBspError, match=message):
        load_app(workspace.app)


def test_merge_defines():
    assert merge_defines(['A=1', 'B', 'C=app'], ['C=cli', 'D=4']) == ['A=1', 'B', 'C=cli', 'D=4']
    with pytest.raises(LeanBspError, match='invalid define'):
        merge_defines([], ['=1'])
    with pytest.raises(LeanBspError, match='invalid define'):
        merge_defines(['1ABC'], [])


BOARD_1_5 = """\
[bsp]
name = "board"
lean_bsp_contract_version = "1.5"
entry_point = "bsp_cli.py"

[bsp.mcu]
vendor = "ST"
part = "STM32U5A5ZJT6Q"
families = ["STM32", "STM32U5", "STM32U5A5"]
reserved = ["RCC", "USART1"]

[[bsp.parts]]
role = "flash"
vendor = "Winbond"
part = "W25Q32RV"
checked = true

[[bsp.targets]]
name = "m33"
cpu = "cortex-m33"
toolchain = "arm-none-eabi"
run_mode = "auto-after-load"
trustzone = "non-secure"

[[bsp.targets]]
name = "rv"
cpu = "rv32imcb"
toolchain = "riscv-none-elf"
run_mode = "auto-after-load"
"""


def test_mcu_parts_and_trustzone(tmp_path):
    (tmp_path / 'bsp.toml').write_text(BOARD_1_5)
    bsp = load_bsp(tmp_path)
    assert (bsp.mcu.vendor, bsp.mcu.part, bsp.mcu.checked) == ('ST', 'STM32U5A5ZJT6Q', False)
    assert bsp.mcu.families == ['STM32', 'STM32U5', 'STM32U5A5'] and bsp.mcu.reserved == ['RCC', 'USART1']
    (flash,) = bsp.parts
    assert (flash.role, flash.vendor, flash.part, flash.checked, flash.families) == (
        'flash', 'Winbond', 'W25Q32RV', True, [])
    assert bsp.target('m33').trustzone == 'non-secure' and bsp.target('rv').trustzone is None
    assert part_defines(bsp, bsp.target('m33')) == [
        'LEAN_BSP_MCU_VENDOR="ST"', 'LEAN_BSP_MCU_PART="STM32U5A5ZJT6Q"', 'LEAN_BSP_MCU_IS_STM32U5A5ZJT6Q=1',
        'LEAN_BSP_MCU_IS_STM32=1', 'LEAN_BSP_MCU_IS_STM32U5=1', 'LEAN_BSP_MCU_IS_STM32U5A5=1',
        'LEAN_BSP_PART_FLASH="W25Q32RV"', 'LEAN_BSP_PART_FLASH_VENDOR="Winbond"', 'LEAN_BSP_PART_FLASH_IS_W25Q32RV=1',
        'LEAN_BSP_TRUSTZONE_NON_SECURE=1',
    ]
    assert 'LEAN_BSP_TRUSTZONE_NON_SECURE=1' not in part_defines(bsp, bsp.target('rv'))


def test_macro_names():
    assert macro_name('non-secure') == 'NON_SECURE'
    assert macro_name('qspi flash') == 'QSPI_FLASH'
    assert macro_name('Nuvoton') == 'NUVOTON'
    assert macro_name('--') == ''


@pytest.mark.parametrize('change, message', [
    (('[bsp.mcu]\nvendor = "ST"\npart = "STM32U5A5ZJT6Q"', '[bsp.misc]\nvendor = "ST"\npart = "x"'),
     'missing \\[bsp.mcu\\]'),
    (('trustzone = "non-secure"', 'trustzone = "nonsecure"'), 'trustzone must be one of'),
    (('role = "flash"', 'role = "--"'), 'no letter or digit'),
    (('reserved = ["RCC", "USART1"]', 'reserved = ["RCC"]\nrole = "x"'), 'unknown key'),
    (('checked = true', 'checked = true\n\n[[bsp.parts]]\nrole = "Flash"\nvendor = "a"\npart = "b"'),
     'two \\[\\[bsp.parts\\]\\] have the role'),
    (('part = "W25Q32RV"\n', ''), 'missing bsp.parts\\[0\\].part'),
])
def test_mcu_and_parts_errors(tmp_path, change, message):
    (tmp_path / 'bsp.toml').write_text(BOARD_1_5.replace(*change))
    with pytest.raises(LeanBspError, match=message):
        load_bsp(tmp_path)


def test_trustzone_needs_1_5(tmp_path):
    text = BOARD_1_5.replace('lean_bsp_contract_version = "1.5"', 'lean_bsp_contract_version = "1.4"')
    text = text.split('[bsp.mcu]')[0] + '[[bsp.targets]]' + text.split('[[bsp.targets]]', 1)[1]
    (tmp_path / 'bsp.toml').write_text(text)
    with pytest.raises(LeanBspError, match='trustzone needs contract 1.5'):
        load_bsp(tmp_path)


def test_a_native_bsp_names_no_mcu(tmp_path):
    (tmp_path / 'bsp.toml').write_text('[bsp]\nname = "host"\nlean_bsp_contract_version = "1.5"\n'
                                       'entry_point = "bsp_cli.py"\n\n[[bsp.targets]]\nname = "native"\n'
                                       'cpu = "native"\ntoolchain = "gcc"\nrun_mode = "native-process"\n')
    bsp = load_bsp(tmp_path)
    assert bsp.mcu is None and bsp.parts == [] and part_defines(bsp, bsp.target(None)) == []


def test_mcu_needs_1_5(tmp_path):
    text = BOARD_1_5.replace('lean_bsp_contract_version = "1.5"', 'lean_bsp_contract_version = "1.4"')
    (tmp_path / 'bsp.toml').write_text(text.replace('trustzone = "non-secure"\n', ''))
    with pytest.raises(LeanBspError, match=r'\[bsp.mcu\] and \[\[bsp.parts\]\] need contract 1.5 or later'):
        load_bsp(tmp_path)
