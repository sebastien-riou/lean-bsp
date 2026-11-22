import json

import pytest

from leanbsp.cli import EXIT_ERROR, EXIT_MANUAL, main


def bdir_of(workspace):
    return workspace.app / 'build' / 'fake' / 'only'


def test_build_from_app_directory(workspace, capfd):
    assert main(['build', '--define', 'OVERRIDDEN=cli']) == 0
    bdir = bdir_of(workspace)
    app_json = json.loads((bdir / '.lean-bsp' / 'app.json').read_text())
    assert app_json['sources'] == [str(workspace.app / 'main.c')]
    assert app_json['include_dirs'] == [str(workspace.app / 'inc')]
    assert app_json['defines'] == ['FROM_APP=1', 'OVERRIDDEN=cli']
    assert app_json['lib'] is None
    record = json.loads((bdir / '.lean-bsp' / 'build-result.json').read_text())
    assert record['bsp'] == {'name': 'fake', 'path': str(workspace.bsp), 'version': '1.2.3'}
    assert record['target'] == 'only'
    assert record['defines'] == ['FROM_APP=1', 'OVERRIDDEN=cli']
    assert record['artifact']['elf'] == str(bdir / 'fake.elf')
    out = capfd.readouterr().out
    assert out.splitlines()[-1].startswith('{"status": "ok"')  # stdout ends with the BSP's JSON line


def test_load_and_run_rederive_the_build_directory(workspace, monkeypatch, capfd):
    assert main(['build']) == 0
    assert main(['load']) == 0
    assert (bdir_of(workspace) / 'loaded').read_text() == 'only'
    monkeypatch.setenv('FAKE_EXIT', '5')
    assert main(['run', '--timeout', '-1', '--until', 'done']) == 5  # the application's exit code becomes ours
    assert json.loads((bdir_of(workspace) / 'ran').read_text())[-4:] == ['--timeout', '-1', '--until', 'done']
    assert '"com_device": "/dev/fake0"' in capfd.readouterr().out


def test_bdir_alone_names_a_build(workspace, tmp_path, monkeypatch):
    bdir = tmp_path / 'elsewhere'
    assert main(['build', '--bdir', str(bdir)]) == 0
    monkeypatch.chdir(tmp_path)  # outside any application: --bdir must be enough
    assert main(['load-run', '--bdir', str(bdir), '--detach']) == 0
    assert '--detach' in json.loads((bdir / 'ran').read_text())


def test_load_run_builds_when_needed(workspace):
    assert main(['load-run']) == 0
    assert (bdir_of(workspace) / 'fake.elf').is_file()


def test_run_without_build(workspace, capfd):
    assert main(['run']) == EXIT_ERROR
    assert 'run lbsp build first' in capfd.readouterr().err


def test_manual_status(workspace, monkeypatch, capfd):
    assert main(['build']) == 0
    monkeypatch.setenv('FAKE_STATUS', 'manual')
    assert main(['load']) == EXIT_MANUAL
    assert 'manual step: forced manual' in capfd.readouterr().err


def test_failed_build_removes_previous_result(workspace, monkeypatch):
    assert main(['build']) == 0
    monkeypatch.setenv('FAKE_STATUS', 'error')
    assert main(['build']) == EXIT_ERROR
    assert not (bdir_of(workspace) / '.lean-bsp' / 'build-result.json').exists()


def test_check_rejects_missing_primitive(workspace, capfd):
    toml = workspace.app / 'app.toml'
    toml.write_text(toml.read_text().replace('uart = true', 'uart = true\nbutton = true'))
    assert main(['check']) == EXIT_ERROR
    assert 'lacks primitive(s) required by demo: button' in capfd.readouterr().out
    assert main(['build']) == EXIT_ERROR  # build runs the same check first


def test_check_rejects_modified_header(workspace, capfd):
    header = workspace.bsp / 'include' / 'bsp_api.h'
    header.write_text(header.read_text().replace('void      bsp_reset(void);', 'void      bsp_reset(int);'))
    assert main(['check']) == EXIT_ERROR
    assert 'differs from the canonical bsp_api.h' in capfd.readouterr().out


def test_env_default_bsp_beats_app_toml(workspace, monkeypatch, capfd):
    monkeypatch.setenv('LEAN_BSP_DEFAULT_BSP', 'elsewhere')
    assert main(['check']) == EXIT_ERROR
    assert "BSP 'elsewhere' is not cloned" in capfd.readouterr().err
    assert main(['check', '--bsp', 'fake']) == 0  # --bsp beats the environment


def test_unknown_bsp_lists_local_ones(workspace, capfd):
    assert main(['check', '--bsp', 'nowhere']) == EXIT_ERROR
    assert 'BSPs available in' in capfd.readouterr().err


def test_bsp_given_as_path(workspace, tmp_path, capfd):
    assert main(['check', '--bsp', str(workspace.bsp)]) == 0


@pytest.mark.parametrize(
    ('argv', 'expected'),
    [
        (['load-run', '--app', '../lean-app-typo'], '--app ../lean-app-typo: {ws}/lean-app-typo does not exist'),
        (['load-run', '--app', 'main.c'], '--app main.c: {ws}/lean-app-demo/main.c is not a directory'),
        (['check', '--bsp', '../lean-bsp-typo'], '--bsp ../lean-bsp-typo: {ws}/lean-bsp-typo does not exist'),
        (['run', '--bdir', 'build/typo'], '--bdir build/typo: {ws}/lean-app-demo/build/typo does not exist'),
        (['run', '--bsp', 'fake'], '{ws}/lean-app-demo/build/fake/only does not exist; run lbsp build first'),
    ],
)
def test_missing_paths_are_named_as_such(workspace, capfd, argv, expected):
    assert main(argv) == EXIT_ERROR
    assert expected.format(ws=workspace.root) in capfd.readouterr().err


def test_existing_dir_without_app_toml(workspace, capfd):
    assert main(['build', '--app', str(workspace.bsp)]) == EXIT_ERROR
    assert 'is not an application: it has no app.toml' in capfd.readouterr().err


def test_no_app_toml(workspace, tmp_path, monkeypatch, capfd):
    monkeypatch.chdir(tmp_path)
    assert main(['build']) == EXIT_ERROR
    assert 'no app.toml' in capfd.readouterr().err


def test_multi_target_needs_target(workspace, capfd):
    toml = workspace.bsp / 'bsp.toml'
    toml.write_text(toml.read_text() + '\n[[bsp.targets]]\nname = "second"\ncpu = "c"\ntoolchain = "t"\nrun_mode = "manual"\n')
    assert main(['build']) == EXIT_ERROR
    assert 'pick one with --target: only, second' in capfd.readouterr().err
    assert main(['build', '--target', 'second']) == 0
    assert (workspace.app / 'build' / 'fake' / 'second' / 'fake.elf').is_file()


def test_list_and_show(workspace, capfd):
    assert main(['list-bsps', '--json']) == 0
    rows = {r['name']: r for r in json.loads(capfd.readouterr().out)}
    assert rows['fake']['cloned'] == str(workspace.bsp)
    assert rows['elsewhere']['cloned'] is None
    assert main(['show-bsp', 'fake']) == 0
    assert 'target:      only (cpu fake-cpu' in capfd.readouterr().out


def test_capabilities_merges_static_and_dynamic(workspace, capfd):
    assert main(['capabilities', '--json']) == 0
    caps = json.loads(capfd.readouterr().out)
    assert caps['name'] == 'fake'
    assert caps['targets'][0]['cflags'] == ['-mfake']
    assert caps['dynamic'] == {'fake': True}


def test_cores_and_each_target_s_core_are_shown(workspace, capfd):
    toml = workspace.bsp / 'bsp.toml'
    text = toml.read_text().replace('[[bsp.targets]]', '[[bsp.cores]]\nid = "big"\ncpu = "fake-cpu"\ncount = 2\n'
                                    'description = "the application cores"\n\n[[bsp.targets]]\ncore_id = "big"', 1)
    toml.write_text(text)
    assert main(['capabilities']) == 0
    out = capfd.readouterr().out
    assert 'core big x2 (fake-cpu): the application cores' in out and 'target only: on core big, cpu fake-cpu' in out
    assert main(['capabilities', '--json']) == 0
    caps = json.loads(capfd.readouterr().out)
    assert caps['cores'] == [{'id': 'big', 'cpu': 'fake-cpu', 'cflags': None, 'count': 2,
                              'description': 'the application cores'}]
    assert caps['targets'][0]['core_id'] == 'big'
    assert main(['show-bsp', 'fake']) == 0
    out = capfd.readouterr().out
    assert 'core:        big x2 (fake-cpu)' in out and '(on core big, cpu fake-cpu' in out


def test_doctor(workspace, capfd):
    assert main(['doctor']) == 0
    assert 'fake: ok' in capfd.readouterr().out


@pytest.mark.parametrize('argv', [['fetch', 'nope'], ['show-bsp', 'nope']])
def test_unknown_names(workspace, argv, capfd):
    assert main(argv) == EXIT_ERROR
    assert "unknown BSP 'nope'" in capfd.readouterr().err
