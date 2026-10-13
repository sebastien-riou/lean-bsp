"""Test double for a BSP entry point: records what lean-bsp asks, answers with a JSON line.

FAKE_STATUS forces the status of build/load/run, FAKE_EXIT the exit_code that run reports."""

import json
import os
import sys
from pathlib import Path


def option(args, name):
    return args[args.index(name) + 1] if name in args else None


def answer(status, end='\n', **fields):
    print(json.dumps({'status': status, **fields}), end=end, flush=True)
    return 1 if status == 'error' else 0


def main():
    verb, args = sys.argv[1], sys.argv[2:]
    if verb == 'capabilities':
        return answer('ok', fake=True)
    forced = os.environ.get('FAKE_STATUS')
    if forced:
        return answer(forced, message=f'forced {forced}')
    bdir = Path(option(args, '--bdir'))
    if verb == 'build':
        app = json.loads(Path(option(args, '--app-json')).read_text())
        elf = bdir / 'fake.elf'
        elf.write_text(json.dumps({'target': option(args, '--target'), 'app': app}))
        print('fake build log line')
        return answer('ok', artifact={'elf': str(elf)})
    if verb == 'load':
        (bdir / 'loaded').write_text(option(args, '--target'))
        return answer('ok')
    if verb == 'run':
        (bdir / 'ran').write_text(json.dumps(args))
        print('fake app output without a newline', end='')  # the JSON then lands on the same line
        return answer('ok', com_device='/dev/fake0', exit_code=int(os.environ.get('FAKE_EXIT', '0')))
    return answer('error', message=f'unknown verb {verb}')


if __name__ == '__main__':
    sys.exit(main())
