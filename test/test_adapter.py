import pytest

from leanbsp.adapter import invoke, parse_result
from leanbsp.errors import LeanBspError
from leanbsp.manifest import load_bsp


def test_parse_last_line():
    assert parse_result('log\nmore log\n{"status": "ok", "x": 1}\n', 'w') == {'status': 'ok', 'x': 1}


def test_parse_json_after_partial_line():
    out = 'app output without newline{"status": "ok", "exit_code": 3}'
    assert parse_result(out, 'w')['exit_code'] == 3


@pytest.mark.parametrize('out', ['', 'just text\n', '{"status": "maybe"}\n', '[1, 2]\n'])
def test_parse_rejects(out):
    with pytest.raises(LeanBspError):
        parse_result(out, 'w')


def test_invoke_streams_and_parses(workspace, capfd):
    result = invoke(load_bsp(workspace.bsp), 'capabilities', ['--json'])
    assert result == {'status': 'ok', 'fake': True}
    assert '"fake": true' in capfd.readouterr().out


def test_invoke_without_echo(workspace, capfd):
    invoke(load_bsp(workspace.bsp), 'capabilities', ['--json'], echo=False)
    assert capfd.readouterr().out == ''


def test_invoke_error_status_with_nonzero_exit(workspace, monkeypatch):
    monkeypatch.setenv('FAKE_STATUS', 'error')
    result = invoke(load_bsp(workspace.bsp), 'load', ['--bdir', str(workspace.root)])
    assert result['status'] == 'error'
