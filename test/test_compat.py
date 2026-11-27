import pytest

from leanbsp.compat import (
    canonical_header,
    contract_file,
    contract_files,
    contract_serves,
    problems,
    supported_contracts,
)
from leanbsp.manifest import load_app, load_bsp


def test_contracts_shipped():
    assert {'1.0', '1.1', '1.2', '1.3', '1.4', '1.5'} <= set(supported_contracts())


def test_contract_1_1_adds_the_cycle_timer_to_1_0():
    old, new = canonical_header('1.0').decode(), canonical_header('1.1').decode()
    assert 'bsp_cycle_timer_start' in new and 'bsp_cycle_timer_start' not in old
    assert '#define LEAN_BSP_CONTRACT_VERSION "1.1"' in new


def test_contract_1_2_describes_the_nvm_regions_further():
    old, new = canonical_header('1.1').decode(), canonical_header('1.2').decode()
    for added in ('write_size;', 'zero_overwrite;', 'BSP_NVM_REFUSED', '.bsp_nvm<N>', 'LEAN_BSP_NVM_REGIONS'):
        assert added in new and added not in old
    assert '#define LEAN_BSP_CONTRACT_VERSION "1.2"' in new


def test_contract_1_3_changes_only_the_nvm_status_comment():
    old, new = canonical_header('1.2').decode(), canonical_header('1.3').decode()
    assert '#define LEAN_BSP_CONTRACT_VERSION "1.3"' in new
    assert 'without programming anything' in old and 'without programming anything' not in new
    assert 'nothing at or above the refused unit' in new and 'Only a write returns BSP_NVM_REFUSED' in new
    # outside that comment, the header line and the version string, the code is 1.2's
    def code(text):
        return [line for line in text.splitlines() if not line.lstrip().startswith(('*', '/*'))
                and 'LEAN_BSP_CONTRACT_VERSION' not in line]
    assert code(old) == code(new)


def _code(text):
    return [line for line in text.splitlines() if not line.lstrip().startswith(('*', '/*'))
            and 'LEAN_BSP_CONTRACT_VERSION' not in line and line.strip()]


def test_contract_1_4_changes_the_meaning_of_the_heap_use():
    old, new = canonical_header('1.3').decode(), canonical_header('1.4').decode()
    assert '#define LEAN_BSP_CONTRACT_VERSION "1.4"' in new
    assert 'heap high-water mark' in old and 'heap high-water mark' not in new
    assert 'bsp_heap.c' in new and 'SIZE_MAX' in new
    strip = lambda lines: [line.split('/*')[0].rstrip() for line in lines]  # noqa: E731
    assert strip(_code(old)) == strip(_code(new))  # the same declarations
    assert contract_files('1.3') == {'include/bsp_api.h': 'bsp_api.h'}
    assert contract_files('1.4') == {'include/bsp_api.h': 'bsp_api.h', 'src/bsp_heap.c': 'bsp_heap.c'}


def test_contract_1_5_adds_only_the_part_macros_comment():
    old, new = canonical_header('1.4').decode(), canonical_header('1.5').decode()
    assert '#define LEAN_BSP_CONTRACT_VERSION "1.5"' in new
    for added in ('LEAN_BSP_MCU_PART', 'LEAN_BSP_MCU_IS_<NAME>', 'LEAN_BSP_PART_<ROLE>', 'LEAN_BSP_TRUSTZONE_OFF'):
        assert added in new and added not in old
    assert _code(old) == _code(new)
    assert contract_file('1.4', 'bsp_heap.c') == contract_file('1.5', 'bsp_heap.c')


def test_a_1_4_bsp_keeps_a_copy_of_the_heap_counter(workspace):
    toml = workspace.bsp / 'bsp.toml'
    toml.write_text(toml.read_text().replace('lean_bsp_contract_version = "1.0"', 'lean_bsp_contract_version = "1.4"'))
    (workspace.bsp / 'include' / 'bsp_api.h').write_bytes(canonical_header('1.4'))
    app, bsp = load_app(workspace.app), load_bsp(workspace.bsp)
    assert problems(app, bsp, bsp.target(None)) == ['BSP fake has no src/bsp_heap.c']
    (workspace.bsp / 'src').mkdir()
    (workspace.bsp / 'src' / 'bsp_heap.c').write_bytes(contract_file('1.4', 'bsp_heap.c') + b'\n')
    (found,) = problems(app, bsp, bsp.target(None))
    assert found.endswith('differs from the canonical bsp_heap.c of contract 1.4')
    (workspace.bsp / 'src' / 'bsp_heap.c').write_bytes(contract_file('1.4', 'bsp_heap.c'))
    assert problems(app, bsp, bsp.target(None)) == []


@pytest.mark.parametrize('bsp_version, app_version, serves', [
    ('1.3', '1.2', True), ('1.2', '1.3', False),
    ('1.2', '1.1', True), ('1.1', '1.2', False), ('1.1', '1.0', True), ('1.1', '1.1', True), ('1.0', '1.1', False), ('2.0', '1.1', False), ('1.0', '1.0', True),
])
def test_contract_serves_earlier_minor_versions(bsp_version, app_version, serves):
    assert contract_serves(bsp_version, app_version) is serves


def test_compatible(workspace):
    bsp = load_bsp(workspace.bsp)
    assert problems(load_app(workspace.app), bsp, bsp.target(None)) == []


def test_missing_primitive(workspace):
    toml = workspace.app / 'app.toml'
    toml.write_text(toml.read_text().replace('uart = true', 'uart = true\nflash_write = true\nled = true'))
    bsp = load_bsp(workspace.bsp)
    found = problems(load_app(workspace.app), bsp, bsp.target(None))
    assert found == ['BSP fake target only lacks primitive(s) required by demo: flash_write, led']


def test_header_copy_must_match(workspace):
    header = workspace.bsp / 'include' / 'bsp_api.h'
    header.write_bytes(header.read_bytes() + b'/* local tweak */\n')
    bsp = load_bsp(workspace.bsp)
    found = problems(load_app(workspace.app), bsp, bsp.target(None))
    assert len(found) == 1 and 'differs from the canonical bsp_api.h' in found[0]


def test_header_copy_missing(workspace):
    (workspace.bsp / 'include' / 'bsp_api.h').unlink()
    bsp = load_bsp(workspace.bsp)
    assert problems(load_app(workspace.app), bsp, bsp.target(None)) == ['BSP fake has no include/bsp_api.h']


def test_contract_mismatch(workspace):
    toml = workspace.app / 'app.toml'
    toml.write_text(toml.read_text().replace('lean_bsp_contract_version = "1.0"', 'lean_bsp_contract_version = "2.0"'))
    bsp = load_bsp(workspace.bsp)
    found = problems(load_app(workspace.app), bsp, bsp.target(None))
    assert found == ['demo targets contract 2.0 but BSP fake implements 1.0']
