"""The shared heap counter (contract 1.4's bsp_heap.c), built with the host's gcc and run."""

import shutil
import subprocess

import pytest

from leanbsp.compat import contract_file

PROGRAM = r"""
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "bsp_api.h"

void *__real_malloc(size_t size);
void __real_free(void *mem);

int main(void) {
    bsp_heap_usage_reset();
    void *a = malloc(1000);
    assert(bsp_heap_usage() == 1000);
    free(a);
    assert(bsp_heap_usage() == 1000);              /* the peak stays */
    bsp_heap_usage_reset();
    assert(bsp_heap_usage() == 0);

    void *c = calloc(10, 20);
    assert(bsp_heap_usage() == 200);
    free(c);

    void *p = malloc(100);                         /* live 100 at the reset: the baseline */
    bsp_heap_usage_reset();
    p = realloc(p, 300);                           /* replaces 100 by 300 */
    assert(bsp_heap_usage() == 200);
    free(p);

    void *before = malloc(500);
    bsp_heap_usage_reset();                        /* baseline 500 */
    free(before);                                  /* live 0, below the baseline */
    void *q = malloc(400);
    assert(bsp_heap_usage() == 0);
    free(q);

    bsp_heap_usage_reset();
    free(NULL);
    void *unknown = __real_malloc(64);             /* never recorded */
    free(unknown);
    assert(bsp_heap_usage() == 0);
    void *r = realloc(NULL, 50);                   /* malloc(50) */
    assert(bsp_heap_usage() == 50);
    r = realloc(r, 0);                             /* glibc frees it and returns NULL */
    if (r) free(r);

    bsp_heap_usage_reset();
    void *blocks[LEAN_BSP_HEAP_MAX_BLOCKS + 1];
    for (int i = 0; i <= LEAN_BSP_HEAP_MAX_BLOCKS; i++) blocks[i] = malloc(8);
    assert(bsp_heap_usage() == SIZE_MAX);          /* one block could not be recorded */
    for (int i = 0; i <= LEAN_BSP_HEAP_MAX_BLOCKS; i++) free(blocks[i]);
    bsp_heap_usage_reset();
    assert(bsp_heap_usage() == 0);
    void *s = malloc(8);
    assert(bsp_heap_usage() == 8);
    free(s);
    puts("heap ok");
    return 0;
}
"""


@pytest.mark.skipif(shutil.which('gcc') is None, reason='no host gcc')
@pytest.mark.parametrize('version', ['1.4', '1.5'])
def test_heap_counter(tmp_path, version):
    heap = contract_file(version, 'bsp_heap.c')
    header = contract_file(version, 'bsp_api.h')
    (tmp_path / 'bsp_api.h').write_bytes(header)
    (tmp_path / 'bsp_heap.c').write_bytes(heap)
    (tmp_path / 'main.c').write_text(PROGRAM)
    exe = tmp_path / 'heap'
    wrap = ','.join(f'--wrap={f}' for f in ('malloc', 'calloc', 'realloc', 'free'))
    build = ['gcc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-fno-builtin',  # keeps unused blocks
             '-DLEAN_BSP_HEAP_MAX_BLOCKS=16',
             f'-I{tmp_path}', str(tmp_path / 'main.c'), str(tmp_path / 'bsp_heap.c'), f'-Wl,{wrap}', '-o', str(exe)]
    subprocess.run(build, check=True, timeout=60)
    done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
    assert done.returncode == 0, done.stderr
    assert done.stdout == 'heap ok\n'
