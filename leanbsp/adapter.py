"""The process-boundary contract: run <bsp>/entry_point <verb> ... and parse its trailing JSON line."""

import json
import os
import subprocess
import sys

from leanbsp.errors import LeanBspError
from leanbsp.manifest import Bsp

STATUSES = ('ok', 'error', 'manual')
_TAIL_BYTES = 1 << 16


def entry_command(bsp: Bsp) -> list[str]:
    entry = bsp.entry_point
    if not entry.is_file():
        raise LeanBspError(f'BSP {bsp.name}: entry point {entry} not found')
    if os.access(entry, os.X_OK):
        return [str(entry)]
    if entry.suffix == '.py':
        return [sys.executable, str(entry)]
    raise LeanBspError(f'BSP {bsp.name}: entry point {entry} is not executable')


def parse_result(output: str, what: str) -> dict:
    """The JSON object on the last non-empty line of `output`.

    The line may start with output from the application itself when it did not end with a newline,
    so the object is also looked for from the last '{"status"' on that line."""
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise LeanBspError(f'{what}: no output, expected a JSON result line')
    last = lines[-1]
    for candidate in (last, last[last.rfind('{"status"') :] if '{"status"' in last else None):
        if candidate is None:
            continue
        try:
            result = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and result.get('status') in STATUSES:
            return result
    raise LeanBspError(f'{what}: last output line is not a JSON result: {last!r}')


def invoke(bsp: Bsp, verb: str, args: list[str], *, interactive: bool = False, echo: bool = True) -> dict:
    """Run one verb, streaming its output through as it arrives unless `echo` is False.

    `interactive` hands lean-bsp's own stdin to the entry point, for a `run` whose application
    talks to the terminal; otherwise its stdin is empty."""
    cmd = [*entry_command(bsp), verb, *args]
    what = f'BSP {bsp.name} {verb}'
    stdin = None if interactive else subprocess.DEVNULL
    with subprocess.Popen(cmd, cwd=bsp.root, stdin=stdin, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        out = sys.stdout.buffer
        tail = bytearray()
        fd = proc.stdout.fileno()
        while chunk := os.read(fd, 65536):
            if echo:
                out.write(chunk)
                out.flush()
            tail += chunk
            if len(tail) > _TAIL_BYTES:
                del tail[:-_TAIL_BYTES]
        returncode = proc.wait()
    result = parse_result(tail.decode('utf-8', errors='replace'), what)
    if returncode != 0 and result['status'] == 'ok':
        raise LeanBspError(f'{what}: reported ok but exited with code {returncode}')
    return result
