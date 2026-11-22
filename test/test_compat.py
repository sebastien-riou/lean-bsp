import pytest

from leanbsp.compat import canonical_header, contract_serves, problems, supported_contracts
from leanbsp.manifest import load_app, load_bsp


def test_contracts_shipped():
    assert {'1.0', '1.1', '1.2'} <= set(supported_contracts())


def test_contract_1_1_adds_the_cycle_timer_to_1_0():
    old, new = canonical_header('1.0').decode(), canonical_header('1.1').decode()
    assert 'bsp_cycle_timer_start' in new and 'bsp_cycle_timer_start' not in old
    assert '#define LEAN_BSP_CONTRACT_VERSION "1.1"' in new


def test_contract_1_2_describes_the_nvm_regions_further():
    old, new = canonical_header('1.1').decode(), canonical_header('1.2').decode()
    for added in ('write_size;', 'zero_overwrite;', 'BSP_NVM_REFUSED', '.bsp_nvm<N>', 'LEAN_BSP_NVM_REGIONS'):
        assert added in new and added not in old
    assert '#define LEAN_BSP_CONTRACT_VERSION "1.2"' in new


@pytest.mark.parametrize('bsp_version, app_version, serves', [
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
