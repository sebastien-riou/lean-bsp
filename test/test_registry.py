from leanbsp.registry import load_registry


def test_builtin_registry_names_boards(monkeypatch, tmp_path):
    monkeypatch.delenv('LEAN_BSP_REGISTRY', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    registry = load_registry()
    assert {'linux-host', 'nucleo-f207', 'nucleo-l552', 'nucleo-u5a5', 'numaker-m5531', 'pico2'} <= set(registry)
    assert 'stm32u5' in registry['nucleo-u5a5'].tags  # the MCU is a tag, the name is the board
    assert all(e.source == 'built-in' for e in registry.values())


def test_extra_registry_overrides_builtin(monkeypatch, tmp_path):
    extra = tmp_path / 'extra.toml'
    extra.write_text('[[bsp]]\nname = "linux-host"\ngit = "https://mirror.invalid/lh.git"\ndefault_ref = "v9"\n')
    monkeypatch.setenv('LEAN_BSP_REGISTRY', str(extra))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'none'))
    entry = load_registry()['linux-host']
    assert (entry.git, entry.default_ref, entry.source) == ('https://mirror.invalid/lh.git', 'v9', str(extra))


def test_config_dir_registry(monkeypatch, tmp_path):
    d = tmp_path / 'lean-bsp' / 'registry.d'
    d.mkdir(parents=True)
    (d / 'org.toml').write_text('[[bsp]]\nname = "in-house"\ngit = "https://gitlab.invalid/in-house.git"\n')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.delenv('LEAN_BSP_REGISTRY', raising=False)
    assert 'in-house' in load_registry()
