from leanbsp.compat import problems, supported_contracts
from leanbsp.manifest import load_app, load_bsp


def test_contract_1_0_is_shipped():
    assert '1.0' in supported_contracts()


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
