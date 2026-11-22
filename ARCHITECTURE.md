# lean-bsp Architecture

This document turns the concept in [README.md](README.md) into a concrete,
implementable design. It is grounded in a close reading of the three legacy
project families, whose repos sit next to this one as siblings — the core
repos `../cm-trace`, `../crypto-benchmark` and `../lean-ftl`, and their
per-board BSP repos named `<project>-<board>` (e.g. `../cm-trace-stm32f207`,
`../crypto-benchmark-rp2350`). They all share the pattern described in
[CLAUDE.md](CLAUDE.md):
a generic core library buildable for several CPU types, plus project-specific
BSP repos (one per board) that implement the missing hardware functions and
own the startup file and `main()`. That pattern scales across boards but not
across projects — a BSP repo is tied to one project, and every core repo
re-implements the same per-CPU build scaffolding.

lean-bsp inverts the relationship: the **application** becomes the generic,
portable artifact (a library with a small number of unimplemented/weak
symbols), and the **BSP** becomes the reusable, project-agnostic piece that
owns `main()`, the startup file, the linker script, and the hardware
primitives — shared across any application that needs that board.

Two sibling repos already exist as placeholders for this model:
`lean-bsp-linux-host` (currently just `README.md`/`LICENSE`) and
`lean-app-hello-world` (`app_main.c`: a stub `app_main()` that prints and
returns). The design below is written to be consistent with both.

## 1. The BSP ↔ application contract

The legacy repos already tried three different override mechanisms, and each
has a known failure mode:

- **cm-trace** mixes true `__attribute__((weak))` symbols (`cmtrace_com_tx`,
  `cmtrace_com_rx`, `cmtrace_clear_caches`) with functions that have *no*
  weak attribute at all (`cmtrace_timer_init/start/stop/read/irq_ack`,
  defaulted in `libcmtrace/source/dwt.c`) and rely instead on static-archive
  link-order semantics — whether a board's own definition wins depends on
  object file ordering, not on the interface. This is fragile and easy to
  get wrong.
- **crypto-benchmark** declares the board functions its core library needs
  (`com_tx`, `com_rx`, plus lean-benchmark's `LBMK_get_cpu_timestamp`,
  `LBMK_init_heap_usage` and `LBMK_get_heap_usage`) as plain `extern`.
  Default implementations exist only for the emulated and host targets
  (`target/<cpu>/` for the Renode CPUs, `target/linux/`), alongside a test
  harness (`test/source/main.c`) that adds `init`, `led1`, `button` and
  `delay_ms`. Each real board reimplements its share in its own `main.c`,
  and nothing checks that the two sides agree. They already don't:
  `lean-benchmark.h` declares `uint64_t LBMK_get_cpu_timestamp()`, while
  `../crypto-benchmark-stm32u5` and `../crypto-benchmark-m5531` define it
  as returning `uint32_t`. Those BSPs never include the header, so no
  compiler sees the mismatch, and the cycle counts come out right only
  because `lbmk.c` truncates `end - start` to 32 bits — the emulated and
  real behavior can silently disagree.
- **lean-ftl** wraps its NVM primitives (`nvm_erase`, `nvm_write`,
  `nvm_read`) behind a function-pointer struct, `lftl_ctx_t`, populated by
  the application. This is the most robust of the three because
  substitutability is explicit, typed data — but the default weak
  implementation is selected and compiled *into the core library itself* at
  build time via a CMake variable (`LFTL_ACCESSORS`), which defeats the goal
  of a BSP owning its own hardware code.

lean-bsp uses a **hybrid, chosen per direction** rather than one mechanism
for everything:

| Relationship | Mechanism | Rationale |
|---|---|---|
| BSP → app hooks (`app_*`) | weak symbols, default owned by the BSP | Exactly one strong definition can exist per link (one app per BSP build), which is the precondition under which weak override is actually safe. |
| app → BSP simple primitives (UART, LED, button, cycle count, CPU frequency, delay, reset, cache clear, heap high-water mark) | plain strong `extern`, **mandatory** for every conforming BSP | No weak fallback exists on this side, so there's nothing for a real board to silently diverge from (crypto-benchmark's failure mode) and no link-order ambiguity (cm-trace's failure mode). |
| app → BSP flash/NVM | function-pointer struct, reusing lean-ftl's proven shape | The one primitive that legitimately needs interchangeable backends and multiple regions; an app can feed a BSP's region list straight into an FTL-style consumer with no new abstraction. |

### `bsp_api.h`

This header is shipped by the lean-bsp framework and included by every BSP
and every application. The canonical copy ships inside the lean-bsp package
(`leanbsp/data/contract/<version>/bsp_api.h`), and every BSP keeps its own copy at
`include/bsp_api.h`, so its vendor IDE project builds and debugs without
lean-bsp installed. `lean-bsp check` fails if a BSP's copy differs from the
canonical one for its `lean_bsp_contract_version`.

```c
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* ---- BSP -> app hooks: app_main required; weak do-nothing defaults for the
 * others live in the BSP's sources, not here (see below) ---- */
int  app_main(void);
void app_init(void);                              /* one-time, IRQs still masked */
void app_tick(uint32_t tick_ms);                  /* called from the BSP tick ISR, every millisecond */
void app_on_fault(uint32_t reason, uintptr_t pc); /* last chance before the BSP's terminal action */
void app_on_exit(int code);                       /* app_main returned code */

enum {
    APP_FAULT_HARD = 0, APP_FAULT_ASSERT, APP_FAULT_STACK_OVERFLOW,
    APP_FAULT_WATCHDOG, APP_FAULT_UNKNOWN
};

/* ---- app -> BSP primitives: strong, BSP-owned, mandatory ---- */
void      bsp_uart_tx(unsigned port, const void *buf, size_t len);   /* blocking */
size_t    bsp_uart_rx(unsigned port, void *buf, size_t len);          /* non-blocking, returns n */
void      bsp_led_set(unsigned id, bool on);
bool      bsp_button_get(unsigned id);
uint32_t  bsp_cycle_count(void);
uint32_t  bsp_cpu_freq_hz(void);
void      bsp_delay_ms(uint32_t ms);
void      bsp_reset(void);
void      bsp_clear_caches(void);        /* invalidate I/D caches and flash prefetch; no-op without caches */
void      bsp_heap_usage_reset(void);    /* restart heap high-water-mark tracking */
size_t    bsp_heap_usage(void);          /* growth of the heap high-water mark since the last reset, in bytes */

/* ---- flash/NVM: function-pointer struct, lean-ftl-compatible shape ---- */
typedef uint8_t (*bsp_nvm_erase_t)(void *base_address, unsigned n_pages);
typedef uint8_t (*bsp_nvm_write_t)(void *dst, const void *src, uintptr_t size);
typedef uint8_t (*bsp_nvm_read_t )(void *dst, const void *src, uintptr_t size);

typedef struct {
    const char       *name;             /* e.g. "internal-flash" */
    void             *base_address;
    uintptr_t         size;
    uintptr_t         page_size;
    bsp_nvm_erase_t   erase;
    bsp_nvm_write_t   write;
    bsp_nvm_read_t    read;
} bsp_nvm_region_t;

/* ---- capability introspection: runtime, const, BSP-populated ---- */
typedef struct {
    const char             *board_name;
    unsigned                n_uarts, n_leds, n_buttons;
    bool                    has_cycle_counter;
    unsigned                n_nvm_regions;
    const bsp_nvm_region_t *nvm_regions;    /* NULL if n_nvm_regions == 0 */
} bsp_capabilities_t;

const bsp_capabilities_t *bsp_get_capabilities(void);
```

The hooks are declared without `__attribute__((weak))` on purpose. GCC
carries a weak declaration over to any definition in the same translation
unit, so an application that includes this header and defines `app_init`
would get a weak definition too; with two weak definitions, link order
picks the winner — cm-trace's failure mode. The BSP marks only its own
default definitions weak.

The last three primitives come straight from the legacy projects. Both
cm-trace (`cmtrace_clear_caches`) and crypto-benchmark (lean-benchmark's
`LBMK_clear_caches`) need the board to flush its caches before a measured
run; on `../crypto-benchmark-stm32u5` that means invalidating ICACHE and
DCACHE and toggling the flash prefetch buffer — silicon knowledge that
belongs in the BSP. crypto-benchmark also reports peak heap usage through
`LBMK_init_heap_usage`/`LBMK_get_heap_usage`, which each of its BSPs
implements inside its own `_sbrk` (`Core/Src/sysmem.c` on stm32u5). In
lean-bsp the BSP owns `_sbrk` along with the startup file and linker script,
so it owns the high-water mark too.

When an application's code was written against another library's hook
names, the application — not the BSP — carries a one-line shim in a file
that includes both headers:

```c
#include <lean-benchmark/lean-benchmark.h>
#include "bsp_api.h"
uint64_t LBMK_get_cpu_timestamp(void) { return bsp_cycle_count(); }
```

That placement is what rules out the return-type mismatch described above:
the shim sees lean-benchmark's prototype, so declaring it with the wrong
type is a compile error instead of a silent ABI mismatch.

Standard output and input are UART 0 on every BSP: the BSP routes `printf`
and friends through `bsp_uart_tx(0, ...)` and `bsp_uart_rx(0, ...)` (newlib's
`_write`/`_read` on bare metal), so an application can use either and get
the same bytes on the same wire. On `linux-host`, UART 0 is the process's
own stdin/stdout when `run` waits for it, and a pseudo-terminal reported as
`com_device` when it runs with `--detach` (§5).

### Contract versions, and the cycle timer (1.1)

A contract only grows within a major version: a BSP serves applications
written for its own version or an earlier minor version of the same major
(`lbsp check`), and keeps the canonical header of the version it
implements. Contract 1.1 adds one optional primitive to 1.0, `cycle_timer`,
for cm-trace:

```c
typedef void (*bsp_cycle_timer_handler_t)(void);
void      bsp_cycle_timer_init(bsp_cycle_timer_handler_t handler);
void      bsp_cycle_timer_start(uint32_t cycles);
void      bsp_cycle_timer_stop(void);
uint32_t  bsp_cycle_timer_read(void);
void      bsp_cycle_timer_ack(void);
```

A one-shot timer counting CPU cycles, whose interrupt preempts everything
else the BSP runs and enters `handler` as the interrupt's own exception
handler, so the handler may rewrite the interrupted context; one more in
`cycles` delays the interrupt by exactly one cycle, and the BSP holds off its
own other interrupts (its tick) while the timer runs. cm-trace's
`cmtrace_timer_*` map onto it one for one. On `nucleo-f207` it is TIM5, which
counts the core clock there, with the handler placed in a RAM copy of the
vector table rather than by editing the vendor startup file. A Cortex-M BSP
could also build it on the DWT comparator and DebugMonitor, as
`libcmtrace/source/dwt.c` does, but that one refuses to run once a debugger
has left halting debug enabled, which every ST-LINK reset does. On RISC-V,
where a trap saves nothing, `handler` stays a plain C function: the BSP's
trap entry saves the registers a call may change and calls it, so the same
handler serves every core. The interrupted pc is `mepc`, which the handler
may read and rewrite; the interrupted registers are out of its reach, the
one thing it has on Cortex-M and not there. The entry is fixed code, so the
contract's one-cycle step holds: `pico2` checks it on its Hazard3 cores
and its Cortex-M33, which share one timer, the RP2350's RISC-V platform
timer (64 bits, so any 32-bit count). All the phase-1
BSPs moved to 1.1; `nucleo-f207`, `nucleo-u5a5` (both on TIM5) and `pico2`
(both targets) offer the cycle timer so far.

### The glue sequence

The entire contract surface inside `main()` is four lines, owned by the BSP:

```c
int main(void) {
    /* vendor-generated bring-up: HAL_Init(), SystemClock_Config(), MX_*_Init() ... */

    /* USER CODE BEGIN 2 */
    bsp_init();                 /* lean-bsp glue: tick timer, cycle counter, capabilities */
    app_init();                 /* weak; IRQs still masked here, deterministic ordering */
    __enable_irq();
    int rc = app_main();
    app_on_exit(rc);             /* weak default: nothing on bare metal, exit(rc) on linux-host */
    /* USER CODE END 2 */

    for (;;) {}
}
```

Returning from `app_main` on a board therefore parks the CPU rather than
resetting it: an application that has finished — a benchmark that has
printed its results — must not start over on its own. One that wants a
restart calls `bsp_reset()` itself.

`app_on_fault` is called from the BSP's own fault handlers (`HardFault_Handler`,
an assert macro, a stack-overflow guard, etc.) immediately before the BSP's
own default terminal action. It is a last-chance-to-react hook — it cannot
resume execution, since the BSP owns the vector table and the decision of
what "recovery" means for that board.

## 2. Linker script decomposition

Today each board hand-rolls one linker script (see
`../cm-trace-stm32f207/STM32F207XX_FLASH.ld`), mixing a
board-specific memory map with boilerplate section layout that's identical
across boards of the same architecture family. lean-bsp splits this into
standard-named fragment files inside each generic BSP's `linker/` directory:

- `linker/memory.ld` — `ENTRY(Reset_Handler)`, the `MEMORY { RAM ... FLASH ... }`
  block, `_estack`, `_Min_Heap_Size`, `_Min_Stack_Size`. The one genuinely
  board-specific file.
- `linker/sections_head.ld` — `.isr_vector` through `.fini_array` (everything
  before `.data`), boilerplate shared across boards in the same family.
- `linker/sections_data.ld` — the `.data` section alone, kept separate
  because applications most often need to customize RAM-function or TCM
  placement right around here.
- `linker/sections_tail.ld` — `.bss`, `._user_heap_stack`, `/DISCARD/`.
- `linker/default.ld` — ready to use as-is:

  ```
  INCLUDE memory.ld
  SECTIONS {
      INCLUDE sections_head.ld
      INCLUDE sections_data.ld
      INCLUDE sections_tail.ld
  }
  ```

An application that needs a custom section copies `default.ld` into its own
repo (e.g. as `app.ld`) and inserts its section between the includes:

```
INCLUDE memory.ld
SECTIONS {
    INCLUDE sections_head.ld
    .my_special_data : { *(.my_special_data) } >RAM
    INCLUDE sections_data.ld
    INCLUDE sections_tail.ld
}
```

GNU ld's `INCLUDE` directive is valid anywhere a script line is, including
inside `SECTIONS {}`, so this directly realizes the README's "a default,
modular, linker script ... made up of several files with standard names such
that an application can make a custom linker script which include them."

The `sections_head/data/tail.ld` triplet is architecture-family boilerplate
(a Cortex-M variant today; a RISC-V variant would need its own set, mirroring
the existing split between `gcc-arm-none-eabi.cmake` and
`gcc-riscv-none-elf.cmake`) and belongs to the reusable, cross-project BSP
content — it should never again be hand-rolled per board.

## 3. CPU-type variation

A BSP is per-board, and a board implies a fixed set of available CPUs — for
the common single-core case, exactly one — so **all CPU-specific
scaffolding — compiler flags, the startup file, CMSIS device headers,
cache-control asm — moves entirely into each generic BSP repo**. lean-bsp
itself no longer needs to be CPU-portable the way the legacy core repos
were; that job now belongs to the BSP.

What does stay shared at the framework level is the *toolchain-family*
layer, not the CPU layer. `cmake/gcc-arm-none-eabi.cmake` is byte-identical
in `../cm-trace` and `../lean-ftl`; `../crypto-benchmark` carries its own
copy under `target/`, which has already drifted from the other two (LTO
flags, `gcc-ar`/`gcc-ranlib`). That drift is exactly the duplication
problem this design removes. None of the three copies carries an `-mcpu`
flag: they only set up compiler/archiver/linker plumbing common to *any*
Cortex-M target. lean-bsp ships one such file per toolchain family
(arm-none-eabi, riscv-none-elf, host gcc for `lean-bsp-linux-host`), and each
BSP's own build picks the matching toolchain file and layers its CPU flags,
startup file, and CMSIS headers on top. The CPU flags themselves are declared
once per target, in `bsp.toml` (`cflags`, §5), so the BSP's own build and an
application that builds its own library compile with the same flags.

Consequence for applications: an app library must never bake in
architecture flags of its own and should never need `#ifdef` per CPU for
ordinary logic — it receives its compiler and flags from the BSP target it
is built for. What an app may legitimately need is the CPU's *name* at build
time, to pick prebuilt per-CPU dependencies: crypto-benchmark links
third-party libraries built separately for each CPU (lean-benchmark,
pqmicrolib, wolfssl, PQRV, …), selected by CPU name in `link_ext.py`, and
some exist only for some CPUs (PQRV is RISC-V only). Each target therefore
declares its `cpu`, and lean-bsp hands it to the application's build (§5).
Where an app genuinely needs runtime numbers, `bsp_cpu_freq_hz()` and
`bsp_get_capabilities()->board_name` cover it.

Declaring the flags once matters in practice. Today crypto-benchmark
compiles its core with its own `target/<cpu>.cmake` flags while each BSP
compiles the rest of the firmware with its own, and the two already differ:
the core library linked into the RP2350 RISC-V firmware is built with
`-march=rv32imc_zbb_zbc_zbs_zbkb_zbkc_zbkx_zicsr`, while pico-sdk builds the
firmware around it with `-march=rv32imac_zicsr_zifencei_zba_zbb_zbs_zbkb`.
Nothing checks that such pairs are compatible.

### AMP SoCs with multiple cores

Some SoCs are AMP (Asymmetric Multi Processor): multiple cores, not all of
the same type, and/or with access to different resources. lean-bsp's scope
for them is deliberately narrow: **exactly one core runs the application**,
the one the chosen `target` names. The BSP may run code of its own on the
other cores, where the board cannot do without it (below); otherwise they
are left in their low-power/parked boot state. Out of scope: more than one
core running application code at once (several applications, or one
application spanning cores, with the inter-core coordination that implies),
and SMP (several identical cores running one program).

The simplest case needs nothing on the other cores, and is how the RP2350 is
handled today, confirmed in
`../crypto-benchmark-rp2350/`. That chip has 2
Cortex-M33 and 2 RV32IMC (Hazard3) cores, of which only one pair — and
within it, only one core — is ever active for this project. A single BSP
repo covers both architectures: its `buildit` script maps a CLI argument to
pico-sdk variables (`rv32imcb` → `PICO_PLATFORM=rp2350-riscv`,
`PICO_GCC_TRIPLE=riscv-none-elf`; `cortex-m33` → `PICO_PLATFORM=rp2350-arm-s`,
`PICO_GCC_TRIPLE=arm-none-eabi`), doing a full `rm -rf build` + reconfigure
per target rather than building both in one tree. No code explicitly parks
or disables the sibling core — it simply stays in its boot-time reset state
because the app never calls the pico-sdk's `multicore_launch_core1()`.
`../crypto-benchmark/hardware-platforms/rp2350.py` already models this at the
tooling level as one board offering two selectable `sw_targets()`
(`cortex-m33`, `rv32imcb`). This case maps directly onto the
`[[bsp.targets]]` array defined in §5, since each entry carries its own
`toolchain`, `cpu` and `cflags`.

The harder case is two cores of the **same** architecture with **different**
resource access (e.g. two Cortex-M33 cores where core 0 and core 1 see
different peripheral or memory maps). Here, `toolchain` and `cpu` can't
disambiguate the targets. Rather than forking into a separate BSP repo per
core — which would fragment one SoC's board-support code across multiple
repos and registry entries for no reason other than a naming collision — a
`target`'s identity should be decoupled from its CPU architecture: its
`name` identifies a *physical core / resource set*, and `toolchain` and
`cpu` are just attributes of it, not the disambiguator. Two `targets`
entries may share the same `toolchain` and `cpu` while differing in
`linker_fragments` (a
different memory map) and in a `primitives_override` table (different
peripheral/resource access) — both already part of the `bsp.toml` schema in
§5.

#### Cores that cannot run alone: companion code

On some boards the core an application wants cannot start by itself:

- **PolarFire SoC** (one E51 monitor hart, four U54 application harts): the
  E51 always boots first, sets up clocks, DDR and I/O, and is the one that
  wakes a U54.
- **STM32H745** (a Cortex-M7 and a Cortex-M4 sharing one flash): each core
  boots from its own flash address, and the M7 normally sets up clocks and
  power for the M4.

The rule above still holds: one core runs the application. What the other
cores must run to make that possible is **companion code**, and it is the
BSP's: it has no application hooks, prints nothing, brings the board up,
starts the application core, then parks. The BSP's own `build` and `load`
produce and load it together with the application, so lean-bsp never sees a
second application, `bsp_api.h` gains nothing, and an application is the
same portable library on such a board as on any other. UART 0, the LEDs and
buttons, the tick and `bsp_reset()` belong to the application core;
`bsp_reset()` restarts the whole board, companions included.

Companion code comes in two shapes:

| Shape | Example | What the BSP's `build` produces |
|---|---|---|
| Same image | PolarFire SoC: Microchip's HAL is one program for all harts, which dispatches on the hart's number | one ELF: the application's hart runs `app_main`, the E51 runs the HAL's hardware setup and wakes it |
| Separate images | STM32H745: the two cores differ in CPU flags and flash address | the application's ELF, plus the BSP's own ELF for the other core |

The same-image shape needs nothing new from lean-bsp or from the loading
tools. The separate-images shape needs a build result that carries several
images and a loader that writes them in one operation, since a second
load would otherwise erase the first; both are added with the first board
that needs them.

On PolarFire SoC, one consequence of the same-image shape is accepted as
Microchip's own arrangement: for a U54 target the whole ELF is compiled for
the U54 (rv64gc), including the HAL code the E51 executes. That code uses no
floating point, and an instruction the E51 lacks traps at once.

#### Describing the cores

`targets` alone is enough for lean-bsp's own operations (build, load,
run, compatibility checking) — it never needs anything more than the target
list. The optional `[[bsp.cores]]` catalog exists one level up: it describes
the board's cores (`id`, `cpu`, `cflags`, `count`, `description`), and a
target's `core_id` names the one its application runs on. That serves two
things `targets` doesn't do on its own:

- **Avoiding duplicated metadata when the same physical core has more than
  one `target`.** This isn't limited to AMP — it also happens on a
  single-core chip that can boot from more than one source. A core that can
  boot from internal NOR flash or from external QSPI flash needs two
  `targets` (`core0_nor`, `core0_qspi`: different `linker_fragments`,
  possibly different `primitives_override` for flash access), but they're
  still the same physical core with the same `cpu`, `cflags` and
  `description`. A shared `core_id` lets both reference one `[[bsp.cores]]`
  entry and inherit those from it, instead of repeating that metadata.
- **Hardware topology independent of build targets.** `targets` only
  lists what's currently *buildable*. A board's physical topology can be
  broader than that — e.g. an SoC with a spare core that has no `target`
  yet because nothing builds for it today. `[[bsp.cores]]` lets
  `lean-bsp show-bsp`/`capabilities` describe "this SoC has these physical
  cores" as its own piece of information, without waiting for every core to
  gain a corresponding buildable target.

`[[bsp.cores]]` is optional — not something every BSP must populate. RP2350
doesn't need it (its two `targets` are already fully self-describing, each
with its own `cpu`); a board with companion code, the same-arch AMP example
and the NOR/QSPI boot-source example all gain from it.

Whichever case applies, what the other cores do — stay unstarted, as on the
RP2350, or run the BSP's companion code and then park — is the BSP's
responsibility for the chosen `target`. It is not a contract primitive
`bsp_api.h` needs to define, since only one core runs the application.

Three illustrative `bsp.toml` fragments — the RP2350 case (targets
disambiguated by architecture), PolarFire SoC (companion code, same image)
and a hypothetical same-arch AMP case (targets disambiguated by `core_id`
and resource overrides):

```toml
# RP2350: two cores of different architectures, same BSP repo
# (cflags as pico-sdk's own toolchain files set them)
[[bsp.targets]]
name = "cortex-m33"
cpu = "cortex-m33"
toolchain = "arm-none-eabi"
cflags = ["-mcpu=cortex-m33", "-mthumb", "-march=armv8-m.main+fp+dsp"]
linker_fragments = ["memory-arm.ld"]
run_mode = "auto-after-load"

[[bsp.targets]]
name = "rv32imcb"
cpu = "rv32imcb"                     # the name apps already use for this core's prebuilt libraries
toolchain = "riscv-none-elf"
cflags = ["-march=rv32imac_zicsr_zifencei_zba_zbb_zbs_zbkb", "-mabi=ilp32"]
linker_fragments = ["memory-riscv.ld"]
run_mode = "auto-after-load"
```

```toml
# PolarFire SoC: the application on the E51, or on the first U54 with the E51 as its companion
[[bsp.cores]]
id = "e51"
cpu = "rv64imac"
description = "monitor hart: boots first, sets the board up, wakes the others"

[[bsp.cores]]
id = "u54"
cpu = "rv64gc"
count = 4
description = "application harts, started by the E51"

[[bsp.targets]]
name = "e51"
core_id = "e51"
toolchain = "riscv-none-elf"
cflags = ["-march=rv64imac_zicsr_zifencei", "-mabi=lp64", "-mcmodel=medany", "-mstrict-align"]
run_mode = "auto-after-load"

[[bsp.targets]]
name = "u54"                         # hart 1; harts 2 to 4 stay parked
core_id = "u54"
toolchain = "riscv-none-elf"
cflags = ["-march=rv64gc", "-mabi=lp64d", "-mcmodel=medany", "-mstrict-align"]
run_mode = "auto-after-load"
```

```toml
# Hypothetical SoC: two same-architecture cores with different resources
[[bsp.cores]]
id = "core0"
cpu = "cortex-m33"
cflags = ["-mcpu=cortex-m33", "-mthumb"]
description = "application core: full peripheral set, external flash"

[[bsp.cores]]
id = "core1"
cpu = "cortex-m33"
cflags = ["-mcpu=cortex-m33", "-mthumb"]
description = "radio core: BLE controller registers, no external flash"

[[bsp.targets]]
name = "core0-app"
core_id = "core0"                    # cpu and cflags inherited from the core
toolchain = "arm-none-eabi"
linker_fragments = ["memory-core0.ld"]
run_mode = "auto-after-load"
[bsp.targets.primitives_override]
flash_read = true
flash_write = true

[[bsp.targets]]
name = "core1-radio"
core_id = "core1"
toolchain = "arm-none-eabi"
linker_fragments = ["memory-core1.ld"]
run_mode = "auto-after-load"
[bsp.targets.primitives_override]
flash_read = false
flash_write = false
```

## 4. Compatibility with silicon-vendor-generated code

This contract is designed to survive being dropped into a silicon vendor's
generated project almost verbatim, which the README requires ("BSP can be
based on Silicon vendors code generator" / "application can be opened with
Silicon vendor tools and debugged as such"). Confirmed against the real
generated files in `../cm-trace-stm32f207`:

- CubeMX wraps customization in `/* USER CODE BEGIN n */ ... /* USER CODE
  END n */` markers and preserves them verbatim across regeneration. The
  four-line glue sequence from §1 fits entirely inside one such block, so a
  generic BSP built from CubeMX stays regeneration-safe.
- `bsp_api.h` and the BSP's own `bsp_*.c` files are added as ordinary extra
  sources/includes — CubeIDE tolerates arbitrary added sources as long as
  generated files outside the USER CODE markers aren't hand-edited.
- The project's generated `*_FLASH.ld` is swapped for the BSP's
  `linker/default.ld` (or an app's custom `.ld`) at the build-configuration
  level, independent of CubeMX regeneration.
- Fault handlers get one added line (a call to `app_on_fault(...)`) inside
  their own USER CODE markers in `Core/Src/stm32*_it.c`, ahead of the
  generated infinite loop.
- `bsp_uart_tx()` and friends are implemented in the BSP's own
  non-generated files on top of the vendor HAL (e.g. `HAL_UART_Transmit(...)`
  inside `bsp_uart.c`) — never by hijacking the vendor's own weak HAL
  callbacks, so the two weak-symbol namespaces stay orthogonal.
- Because the application is a plain static library with no `main()`,
  startup, or default linker script of its own, it folds into the vendor
  IDE project as an ordinary referenced library or extra source folder — the
  project remains one single project, openable and debuggable with vendor
  tools exactly as the README requires.

The contract itself is pure C — one header plus a handful of `.c`/`.ld`
files — with no build-system dependency baked in (unlike lean-ftl's
`LFTL_ACCESSORS` CMake-variable selection, which forces a specific build
system). That means it plugs unchanged into every BSP build system already
observed across the legacy repos: plain STM32CubeMX-generated Makefiles,
CMake + vendor SDK (Raspberry Pi Pico SDK), and a hand-written Makefile
over CMSIS-Toolbox packs (`../crypto-benchmark-m5531`, which still keeps its
csolution/cproject YAML alongside) — without lean-bsp needing to pick a
winner.

## 5. The Python CLI, BSP adapter contract, and registry

### Process-boundary contract, not import-boundary

lean-bsp never imports a BSP's Python code in-process, and it never mandates
a shared build backend. This generalizes the closest existing prior art —
`../crypto-benchmark/hardware-platforms/{stm32u5,rp2350,m5531}.py`,
where each board already exposes `sw_targets()`/`build_cmd()`/`load_cmd()`/`run_cmd()`
functions that *describe* a subprocess command rather than running vendor
logic in-process. The m5531 helper's `run_cmd()` still carries, commented
out since its `./run` script became scriptable, an
`input("Start the firmware manually ... then press ENTER")` fallback from
when that board's run step needed a human.

Every BSP exposes:

1. A static manifest, **`bsp.toml`**, at the BSP repo root — pure data,
   parsed with the standard library's `tomllib`, so browsing and
   compatibility-checking a BSP requires no tool execution at all.
2. One executable **entry point** (declared in the manifest, e.g.
   `entry_point = "bsp_cli.py"`) that lean-bsp always invokes via
   `subprocess`, answering four verbs. Nothing requires the entry point to
   be Python — only that some runnable program answers these verbs. A
   `.py` entry point is run with lean-bsp's own interpreter, whatever its
   shebang would find, so it can import lean-bsp's helpers (below):

   | verb | invocation | result (last stdout line, JSON) |
   |---|---|---|
   | `capabilities` | `entry_point capabilities --json` | dynamic capability data, merged by lean-bsp with the static `bsp.toml` |
   | `build` | `entry_point build --app-json <path> --target <t> --bdir <dir>` | `{"status": "ok\|error\|manual", "artifact": {"elf": ..., "bin": ..., "hex": ...}}` |
   | `load` | `entry_point load --bdir <dir> [--target <t>]` | `{"status": "ok\|error\|manual"}` |
   | `run` | `entry_point run --bdir <dir> [--target <t>] [--detach] [--timeout <s>] [--until <text>]` | `{"status": "ok\|error\|manual", "com_device": ..., "exit_code": ...}` |

   `status: "manual"` formalizes that m5531-style workaround as a first-class
   outcome, not an ad hoc hack, for BSPs whose build/flash step genuinely
   requires a human (a vendor IDE with no headless mode, for instance).

   `--app-json` names the resolved application, which lean-bsp writes to
   `<bdir>/.lean-bsp/app.json` before every build: absolute paths of its
   source files and include directories, its defines (`app.toml`'s merged
   with `--define`), its custom linker script if any, and — for an
   application with `[app.build]` — the static library to link instead of
   sources. A BSP reads that file rather than parsing `app.toml`, so no BSP
   carries its own `app.toml` parser and the schema can evolve without
   touching every BSP.

   `run` starts the firmware from reset. Where completion is observable —
   `run_mode` `native-process` or `emulator` — `run` waits for the app to
   exit, forwarding its output and reporting `exit_code`. On a board there is
   no general way to observe a bare-metal `app_main` returning, so
   `exit_code` is `null`; a board BSP that can follow its console (through
   lean-hwbridge, below) streams it instead, until the text given with
   `--until <text>` shows up, `--timeout` expires or Ctrl-C — the firmware
   keeps running either way, and `--until` failing to show up within the
   timeout is an error, so `lbsp build-load-run --until 'hello-world done'
   --timeout 60` is a test. `--detach` makes `run` return as soon as the app
   is running. `--timeout <s>` defaults to none (`-1` also means wait
   forever), so a debugger can sit on a breakpoint indefinitely.

   Board BSPs reach the hardware through
   [lean-hwbridge](https://github.com/sebastien-riou/lean-hwbridge) (`hwbridge`
   on PATH, or the command in `HWBRIDGE`): it flashes, resets, powers boards
   through a usbman hub and relays their serial ports, for a sandboxed
   session as for a terminal, and lets a BSP capture a boot report from its
   first byte. The board is the usbman nickname in `HWBRIDGE_BOARD`, by
   default the BSP name. Without a bridge, `load` and `run` report
   `manual`, with the command to run by hand.

   `com_device` is the path a host-side tool opens to talk to the app's
   UART 0: a USB serial device on a board, or `hwbridge:<nickname>` when the
   BSP goes through lean-hwbridge; on `linux-host`, a
   pseudo-terminal when run with `--detach` (crypto-benchmark's own linux
   target already opens one through `/dev/ptmx` and prints its name) and
   `null` when `run` waits, since UART 0 is then the terminal; or `null`
   when nothing is reachable. Finding it is the BSP's job, as each crypto-benchmark BSP's
   `find-uart` script already does. `run` reports it rather than `load`,
   because a board whose USB serial port is provided by the MCU itself
   re-enumerates on every reset. A host-side tool connects only after `run`
   has returned: `load` may already have started an instance, and on the
   m5531 that instance boots while the debugger is still detaching, so its
   cycle counter reads zero (see the comment in
   `../crypto-benchmark/benchmark.py`). lean-bsp's own `run` and composite
   commands end their output with the BSP's JSON result line, so a
   host-side tool can script against `com_device` directly.

   After a successful build, lean-bsp — not the BSP — writes
   `<bdir>/.lean-bsp/build-result.json`, recording the artifact paths
   the BSP returned together with the build's provenance: the BSP's registry
   name and the local path it resolved to, the `target`, the application
   path, the `--define` values, and the contract version. That makes a build directory fully
   self-describing, which is what lets `load` and `run` work from
   `--bdir` alone. Keeping this bookkeeping on lean-bsp's side also
   spares every BSP author from reimplementing it — a BSP need not even know
   the registry name it was fetched under. If the recorded BSP path has since
   moved, lean-bsp re-resolves it from the recorded name.

lean-bsp mandates the *contract* (manifest + subprocess verbs) for every
BSP, which is what makes arbitrary build systems supportable from day one —
a legacy vendor Makefile, a CMSIS-Toolbox csolution, or a vendor IDE can all
be wrapped by a few lines of `entry_point` glue that shells out to `make`,
`cmake --build`, `cbuild`, or prints the manual-step message. It does **not**
mandate a specific build tool. For *new* BSPs, lean-bsp ships helper
modules, `leanbsp.backends`, so a new BSP's entry point can call into them
in a few lines rather than hand-rolling subprocess plumbing — while nothing
requires a BSP to use them:

- `board` answers the four verbs around a description of the board (its
  title, and how to flash and run it by hand) and a build function:
  results, the build directory's state, and `load`/`run` through
  lean-hwbridge. All the phase-1 board BSPs are built on it; their
  `bsp_cli.py` is the board's data plus, for pico2, its CMake step.
- `gcc` is the plain gcc build four of them share: the BSP's sources and
  the application's compiled in parallel with the target's `cflags`, linked
  with one linker script, then the HEX and BIN images. `CROSS_COMPILE`
  names the toolchain and must match the target's `toolchain` triple.
- Later: `cmake_generic`, extracted from the confirmed near-identical
  `cmake/generic_binary.cmake` + `cmake/git_version.cmake` + toolchain-file
  scaffold duplicated across all three legacy repos.

### Manifest schemas

`bsp.toml`, in the BSP repo root:

```toml
[bsp]
name = "linux-host"
lean_bsp_contract_version = "1.1"    # version of the app_*/bsp_* contract this BSP implements
version = "0.3.0"
description = "Run/debug apps as a native Linux process"
maintainer = "sebastien.riou@pqshield.com"
entry_point = "bsp_cli.py"

[bsp.hardware]
flash_bytes = 0
ram_bytes = 0

[bsp.build]
backend = "custom"                   # cmake-generic | make | cmsis-toolbox | custom (informational only)
requires_tools = ["gcc", "make"]     # used by `lean-bsp doctor`

[bsp.setup]
steps = []                           # idempotent commands `lean-bsp fetch` runs from the BSP root,
                                     # e.g. ["./setup-pico-sdk", "./setup-picotool"] for rp2350

[bsp.primitives]                     # default primitive support; overridable per target
uart = true
led = false
button = false
cpu_cycle_counter = true
cpu_frequency = true
cache_clear = false
heap_usage = true
flash_read = false
flash_erase = false
flash_write = false

[[bsp.targets]]
name = "native"
cpu = "native"                       # linux-host deliberately names no CPU architecture:
                                     # it builds and runs on whatever machine you are on
toolchain = "gcc"                    # host default compiler; or a full triple for a
                                     # cross-toolchain, e.g. "arm-none-eabi-gcc"
cflags = []                          # CPU/ABI flags for every object linked into this target,
                                     # the application's own library included
linker_fragments = []                # standard-named linker-script include files this target provides
run_mode = "native-process"          # native-process | auto-after-load | debugger-attach | manual | emulator
# core_id = "core0"                  # optional: the [[bsp.cores]] entry the application runs on, for AMP boards
                                     # where target identity isn't fully captured by name/cpu alone;
                                     # the target then inherits cpu and cflags from that core
# [bsp.targets.primitives_override]
# flash_write = true                 # overrides [bsp.primitives] for this target only
```

A `target` here is a build variant the BSP offers — a physical
core/resource-set on an AMP board, or a boot source on a single-core chip —
and its `name` identifies it. Note that this is neither of the two things
"target" usually means elsewhere: it is not an architecture triple (as in
`cargo build --target`) and not a build-system artifact (as in
`cmake --build . --target`). The CPU is merely one attribute of a target,
carried in `cpu`, alongside its compiler flags (`cflags`) and compiler
family (`toolchain`); the board is named by `--bsp`. Those three are
per-target rather than per-BSP because one board can offer several CPUs —
the RP2350 offers two. See §3's
"AMP SoCs with multiple cores" for the full rationale and worked examples,
including the optional `[[bsp.cores]]` catalog that a `core_id` can
reference.

`linux-host` is the one BSP where the CPU is deliberately left unspecified.
Its purpose is to build and run an application natively on the developer's
own machine, so the underlying architecture is whatever that machine
happens to be — x86-64, Arm, or anything else that runs Linux. It therefore
declares a single `native` target (`cpu = "native"`, no `cflags`) built with
the host's default compiler, and neither its manifest nor its registry entry
enumerates host architectures: there is nothing to choose between, and
listing specific ones would only imply a portability limit the BSP doesn't
have.

`app.toml`, in the application repo root:

```toml
[app]
name = "hello-world"
lean_bsp_contract_version = "1.1"
version = "0.1.0"
default_bsp = "linux-host"           # optional: which BSP to use when --bsp is not given

[app.requires]                       # subset of bsp.primitives this app actually calls
uart = true
cpu_cycle_counter = false

[app.sources]                        # the BSP's build compiles these files itself
files = ["app_main.c"]
include_dirs = ["."]
defines = []

# [app.build]                        # alternative to [app.sources]: the app builds its own
# command = ["./lean-bsp-build"]     # static library (see below)

[app.linker]
custom_script = ""                   # optional path to an app-authored top-level .ld
```

`default_bsp` is a convenience, not a declaration of allegiance: it says
which BSP to use when the user names none, and nothing more — the
application remains buildable against any BSP whose primitives satisfy
`app.requires`. `linux-host` is the natural value for most applications,
since it is the one BSP that works on any developer's machine, so a fresh
clone builds and runs with no hardware attached and no options typed. An
application that is only meaningful on real hardware can of course name that
board instead, and one that would rather force an explicit choice simply
omits the key.

A BSP is therefore resolved from the first of: an explicit `--bsp`, the
`LEAN_BSP_DEFAULT_BSP` environment variable (so an engineer working on one
board all week can override every app's default once, in their shell), then
`app.toml`'s `default_bsp`. If none of the three yields a BSP, the command
fails and lists the BSPs available locally. If the resolved BSP is known to
the registry but not cloned yet, the error names the exact
`lbsp fetch <name>` command to run rather than fetching implicitly — a build
should not reach the network on its own.

A BSP *name* resolves to the directory `lean-bsp-<name>` in the workspace:
the directory that holds the application's repository (the parent of its
git root), which is where `fetch` clones it. `--bsp` also accepts a path,
for a BSP checked out anywhere else; a value containing `/` is a path.

Compatibility checking (for a chosen `target`): every key set to `true`
in `app.requires` must also be `true` in the BSP's effective primitives
(defaults merged with any per-`target` override),
`lean_bsp_contract_version` must match, and the BSP's `include/bsp_api.h`
must be identical to lean-bsp's canonical header for that version. A
mismatch fails fast, listing exactly which primitives, version or header
are wrong — this is the guard that
replaces crypto-benchmark's silent divergence between emulated and real
boards.

lean-bsp itself never generates a linker script or invokes a compiler
directly: it resolves `--app`/`--bsp` and the `target` (see below —
omitting `--target` is fine when the BSP declares only one), runs the
compatibility check, writes the resolved `app.json`, and invokes the BSP's
`entry_point build`. The BSP's own native build system does the actual
compiling and linking.

That covers an application small enough to be a list of sources. An
application with a build of its own declares `[app.build]` instead —
crypto-benchmark, for instance, is a CMake project parameterized by
`CRYPTO_LIB`, `ALGO`, `PSET` and `GOAL` that links prebuilt per-CPU
third-party libraries. lean-bsp runs its `command` from the application
root before invoking the BSP, passing each `--define K=V` as a trailing
`K=V` argument (in this mode the defines go to the application's command,
not to the BSP) and setting:

| variable | value |
|---|---|
| `LEAN_BSP_CPU`, `LEAN_BSP_TOOLCHAIN` | the target's `cpu` and `toolchain` |
| `LEAN_BSP_CFLAGS` | the target's `cflags`, space-separated |
| `LEAN_BSP_TOOLCHAIN_FILE` | lean-bsp's toolchain-family CMake file for that `toolchain` (§3) |
| `LEAN_BSP_APP_LIB` | where to write the static library: `<bdir>/app/libapp.a` |

The command must write that library or fail, and may reject a `cpu` it has
no dependencies for. lean-bsp then records the library in `app.json`
(`lib`), and the BSP links it instead of compiling sources.
lean-bsp still invokes no compiler itself, and the CPU flags still come only
from the BSP target. This is the shape all three crypto-benchmark BSPs
already use — each links `../crypto-benchmark/build/<cpu>/libcrypto-benchmark.a`
— except that the flags now flow from the BSP rather than from
crypto-benchmark's own `target/<cpu>.cmake`.

### CLI surface

```
lean-bsp list-bsps [--tag <t>] [--json]
lean-bsp show-bsp <name>
lean-bsp fetch <name> [--ref <tag|branch|sha>] [--dir <path>]
lean-bsp registry add <url-or-path>
lean-bsp registry list
lean-bsp doctor [--bsp <name>]
lean-bsp check [--app <path>] [--bsp <name-or-path>] [--target <t>]
lean-bsp build [--app <path>] [--bsp <name-or-path>] [--target <t>] [--bdir <dir>] [--define K=V]...
lean-bsp load  [--bdir <dir>] [--app <path>] [--bsp <name-or-path>] [--target <t>]
lean-bsp run   [--bdir <dir>] [--app <path>] [--bsp <name-or-path>] [--target <t>] [--detach] [--timeout <s>] [--until <text>]
lean-bsp build-load-run [--app <path>] [--bsp <name-or-path>] [--target <t>] [--bdir <dir>] [--define K=V]... [--detach] [--timeout <s>] [--until <text>]
lean-bsp load-run [--bdir <dir>] [--app <path>] [--bsp <name-or-path>] [--target <t>] [--detach] [--timeout <s>] [--until <text>]
lean-bsp capabilities [--bsp <name-or-path>] [--json]
```

Every one of these also answers to `lbsp`, a second console script for the
same entry point (see Packaging below), so `lbsp build --app ../lean-app-a
--bsp nucleo-u5a5` is the same command as `lean-bsp build ...`.

`--app` is optional, and defaults to the application enclosing the current
directory: lean-bsp looks for `app.toml` in the working directory and then
in its ancestors, stopping at the enclosing git repository — the same search
`git`, `cargo` and `npm` do for their own root files. That makes the
everyday workflow free of redundant paths:

```
git clone .../lean-app-hello-world && cd lean-app-hello-world
pipenv install .
pipenv run lbsp build        # app from the current directory, BSP from app.toml's default_bsp
pipenv run lbsp load-run     # → prints "hello world" on the developer's own machine
pipenv run lbsp build-load-run --bsp nucleo-u5a5      # same app, real hardware, one option
```

Two rules keep this default from ever guessing wrong. It never falls back to
a directory that has no `app.toml`: when the search finds none, the command
fails saying so, rather than proceeding with something unintended. And it
never displaces an explicit address — `--bdir` alone still names a build
directly, so `lbsp run --bdir <dir>` issued from inside an unrelated
application operates on the directory given, not on the enclosing app.

The `pipenv install .` step above implies the application repo carries a
small `pyproject.toml` of its own whose only dependency is `lean-bsp` — a
convenient way for an application to pin the tool version it is known to
build with. That is optional: a globally installed lean-bsp works the same.

`--target` is optional everywhere. When a BSP declares exactly one
`target` — the common case: a single-CPU MCU that always boots from
internal flash — lean-bsp resolves it automatically and the user never has
to name the only thing there is to choose. When a BSP declares more than
one, omitting `--target` is an error that lists the available targets
rather than guessing: silently picking the wrong core or the wrong boot
source would be worse than failing up front. Either way lean-bsp passes the
*resolved* target explicitly to the BSP's entry point, so the adapter
contract above never has to handle an unspecified target.

`--bdir` is optional too, and defaults to
`<app-dir>/build/<bsp>/<target>`. All three components matter: build
output belongs to the application (matching the legacy convention, where
cm-trace built into `cm-trace/build/<cpu>/`), one application may be built
for several BSPs, and one BSP may offer several targets. Keying the default
on the full `(app, bsp, target)` triple means builds of different apps,
BSPs or targets never clobber each other, so this sequence needs exactly two
builds and no rebuilds:

```
lean-bsp build --app ../lean-app-a --bsp nucleo-u5a5      # → ../lean-app-a/build/nucleo-u5a5/cortex-m33
lean-bsp build --app ../lean-app-b --bsp nucleo-u5a5      # → ../lean-app-b/build/nucleo-u5a5/cortex-m33
lean-bsp load-run --app ../lean-app-a --bsp nucleo-u5a5   # reuses app A's build
lean-bsp load-run --app ../lean-app-b --bsp nucleo-u5a5   # reuses app B's build
```

Builds that differ only in their `--define` values do share a directory,
each replacing the previous one. That suits a loop that builds, loads and
runs one variant at a time — which is what crypto-benchmark's benchmark
driver does for each `CRYPTO_LIB`/`ALGO`/`PSET`/`GOAL` combination — and
`build-result.json` records which defines the current build used. To keep
several variants side by side, give each its own `--bdir`.

The two composite commands differ in exactly this respect, and their names
say which is which. `build-load-run` always builds — it is the
`buildit && flash` equivalent, and a fresh build every time is the point.
`load-run` is the counterpart for alternating between existing builds, as
above: it uses whatever build is already in the directory and builds only if
there is none. Given `--bdir` alone, `load-run` requires an existing
build, since a bare directory carries no application or BSP to build from.

`load`, `run` and `load-run` all accept the same two ways of naming a build,
neither of which requires remembering a path or repeating an option:

- app + BSP (plus `--target` only for a multi-target BSP) — re-derives the
  same directory `build` used. Both halves resolve on their own: the app
  from `--app` or the current directory, the BSP from `--bsp`,
  `LEAN_BSP_DEFAULT_BSP` or `app.toml`. From inside an application whose
  `default_bsp` is the one you want, `lbsp load-run` is the whole command.
- `--bdir <dir>` alone — since `build-result.json` records the BSP, the
  target and the artifact, the directory identifies itself and nothing else
  need be given, not even `--bsp`.

An explicit `--bdir` is also how you step outside the default layout
entirely, for a read-only app checkout or a CI scratch area.

Example session:

```
lean-bsp fetch linux-host
lean-bsp build --app ../lean-app-hello-world --bsp linux-host   # one target: nothing to choose
lean-bsp run   --app ../lean-app-hello-world --bsp linux-host   # same app+bsp → same build dir

lean-bsp fetch stm32u5 --ref v1.2.0
lean-bsp build --app ../lean-app-hello-world --bsp nucleo-u5a5  # single CPU, boots from internal flash
lean-bsp load  --app ../lean-app-hello-world --bsp nucleo-u5a5

# pico2's RP2350 offers both an Arm and a RISC-V target, so the choice must be explicit
lean-bsp build --app ../lean-app-hello-world --bsp pico2 --target rv32imcb
```

### Registry and fetch

A built-in registry ships as package data, `leanbsp/data/registry.toml`:

```toml
[[bsp]]
name = "linux-host"
git = "https://github.com/sebastien-riou/lean-bsp-linux-host.git"
description = "Run/debug apps as a native Linux process; day-1 cross-platform target"
tags = ["host", "linux", "native"]
# no default_ref: fetch tracks the default branch

[[bsp]]
name = "nucleo-u5a5"
git = "https://github.com/sebastien-riou/lean-bsp-nucleo-u5a5.git"
default_ref = "v1.2.0"               # what fetch checks out when --ref is not given
description = "ST NUCLEO-U5A5ZJ-Q board"
tags = ["arm", "cortex-m33", "stm32u5"]
```

A BSP is named after its board, not its MCU — it is a *board* support
package, and two boards built around the same MCU still differ in pins,
clocks, LEDs, buttons and debug probe. The repo is `lean-bsp-<board>`
(`lean-bsp-nucleo-u5a5`, `lean-bsp-pico2`), the registry and `--bsp` name is
the board alone, and the MCU goes in `tags`, so
`lean-bsp list-bsps --tag stm32u5` still finds every board built around it.

`lean-bsp registry add <url|path>` appends an extra registry file, resolved
from `~/.config/lean-bsp/registry.d/*.toml` or the `LEAN_BSP_REGISTRY`
environment variable; built-in and extra registries are merged by name, with
an extra entry winning on collision (so an org can override or pin a public
entry). `lean-bsp fetch <name>` runs a plain
`git clone [--branch <ref>] <url> ../lean-bsp-<name>` — a sibling directory,
matching the convention README.md already states and that the existing
`lean-bsp-linux-host` / `lean-app-hello-world` placeholders already assume —
via `subprocess` calling the system `git` binary, with no GitPython
dependency. The ref is `--ref` if given, else the entry's `default_ref`,
else the repo's default branch. After cloning, `fetch` runs the BSP's
`[bsp.setup] steps` from the clone's root. Fetching a BSP that is already
cloned skips the clone but still runs those steps — which is why they must
be idempotent — and warns if the clone has local changes or is not at the
requested ref, so re-running `fetch` repairs a half-set-up BSP without
touching work in progress. The closest prior art is crypto-benchmark's own
`../crypto-benchmark/initial-setup`, driven by
`../crypto-benchmark/setup_manifest.py`: it already clones each BSP and
third-party library as a sibling repo (`git clone --depth=1 --branch
<pinned tag>`), builds https or ssh URLs from a per-host template, flags
entries that need `gitlab.pqsh.net` access as `internal`, and runs each
BSP's idempotent setup steps (`./setup-pico-sdk`, `./setup-picotool`,
`pipenv install`) even when the clone already exists. lean-bsp's fetch is
also deliberately simpler than lean-ftl's packaged-tarball fetch
(`get-lean-ftl` / `get-local-lean-ftl`); a `--release <version>`
variant reusing that idea remains a reasonable future addition for BSPs that
publish packaged releases, but sibling-repo-as-live-git-clone is the default.

**Open deployment question, not solvable by the tool itself:** the registry
entries above point at `github.com/sebastien-riou/...`, matching the
existing sibling repos' remotes, but this org's sandboxed CI environments
are documented as reaching PyPI/npm/crates.io/the Go module proxy/RubyGems
plus the internal `gitlab.pqsh.net`/`sw.pqsh.net` — not `github.com`. If
`fetch` needs to work unattended in such an environment, GitHub-hosted BSPs
need a `gitlab.pqsh.net` mirror, or the sandbox's network allowlist needs to
be extended. This is a decision for whoever operates that environment, not
something lean-bsp's design can paper over.

### Packaging

lean-bsp's own Python package follows the same convention already used by
`lean-com` and `lean-init`: `hatchling` + `hatch-vcs` as the build backend
(version derived from git tags), console-script entry points, and the same
ruff/black/mypy configuration block. Two entry points are declared, so the
tool answers to a short name as well as the canonical one:

```toml
[project.scripts]
lean-bsp = "leanbsp.cli:main"
lbsp     = "leanbsp.cli:main"
```

Both are real console scripts generated at install time, not shell aliases:
they work identically in scripts, Makefiles, CI and non-interactive shells,
on every platform, with no per-user shell configuration. `lean-bsp` stays
the canonical name used throughout this document and in the registry, and
`lbsp` is the equivalent short form for typing by hand — every command below
accepts either spelling. The only caveat is the usual one for short binary
names: if another installed package also ships an `lbsp` script, whichever
comes first on `PATH` wins.

`requires-python >= 3.11` is chosen specifically so the standard
library's `tomllib` can parse every manifest and registry file with zero
third-party TOML dependency; git fetch and BSP-adapter invocation both go
through the standard library's `subprocess`; the CLI itself is built on
`argparse` rather than click or typer. The target is an empty runtime
`dependencies = []`, with `[project.optional-dependencies] dev =
["pytest", "ruff", "black", "mypy"]` mirroring the sibling repos' dev
tooling.

### Module layout

```
lean-bsp/
  pyproject.toml            # hatchling + hatch-vcs, requires-python >= 3.11, [project.scripts]
  hatch.toml
  leanbsp/
    __init__.py
    __main__.py             # python -m leanbsp, same as lean-bsp / lbsp
    cli.py                  # argparse dispatch for the command surface above
    flow.py                 # which app/BSP/target/bdir a command means; build/load/run, app.json, build-result.json
    manifest.py             # tomllib-based loaders/validators for bsp.toml / app.toml
    registry.py             # load + merge built-in and extra registries, name lookup
    fetch.py                # subprocess git clone/checkout, then the BSP's [bsp.setup] steps
    adapter.py              # subprocess contract: invoke <bsp>/entry_point <verb> ..., parse trailing JSON line
    compat.py               # primitive / contract-version / bsp_api.h compatibility checks
    workspace.py            # app.toml discovery, sibling-directory resolution (../lean-bsp-<name>)
    errors.py
    appbuild.py             # later phase: run app.toml's [app.build] command with the LEAN_BSP_* environment
    backends/               # optional helpers for BSP entry points
      board.py              # the four verbs, build-directory state, load/run through lean-hwbridge
      gcc.py                # a plain gcc build: sources + application, one linker script, HEX and BIN
      cmake_generic.py      # later phase: the legacy repos' shared CMake scaffold
    data/
      registry.toml         # built-in known-BSP list
      contract/<version>/bsp_api.h  # canonical header, one directory per contract version (1.0, 1.1)
  test/
    conftest.py             # a throwaway workspace: an app repo next to a cloned fake BSP
    test_manifest.py
    test_registry.py
    test_compat.py
    test_adapter.py
    test_cli.py             # every command end to end against the fake BSP
    test_fetch.py           # fetch from a local git repository, no network
    test_linux_host.py      # hello-world on the real ../lean-bsp-linux-host, when cloned and gcc exists
    fixtures/fake-bsp/{bsp.toml,bsp_cli.py}
```

## 6. Migration strategy

### Order: hello-world → cm-trace → lean-ftl → crypto-benchmark

**hello-world** ensure the general flow is convenient enough. Allow to check
all the boards we will need fit into the BSP pattern: `nucleo-u5a5`,
`nucleo-l552`, `nucleo-f207`, `numaker-m5531`, and `pico2` with both its
`cortex-m33` and `rv32imcb` targets.

To actually prove that fit, hello-world exercises every mandatory primitive
— UART, LED, button, cycle counter, CPU frequency, delay, cache clear, heap
usage and reset — and prints a report of what each returned. Phase 1 is
done when:

1. `lbsp build-load-run` prints that report on `linux-host` and on all six
   board targets above;
2. `lbsp check` rejects a BSP that lacks a primitive the application
   requires, and one whose `include/bsp_api.h` differs from the canonical
   header;
3. one board's firmware is opened and debugged in its vendor IDE, from the
   BSP's own project.

All three are met: the report on all seven targets, `lbsp check`'s
rejections under test, and `nucleo-f207` debugged in STM32CubeIDE 2.0.0,
which imports the ELF lean-bsp builds and finds the application's, the
BSP's and the HAL's sources through its debug information (that BSP's
README has the steps). The IDE debugs; lean-bsp keeps building.

Phase 1 covers applications given as source files only. `[app.build]` and
`lean-bsp registry add` wait for the phases that need them; `[[bsp.cores]]`
arrived with the first board that has companion code (§3).

**cm-trace** It has only one real BSP (`cm-trace-stm32f207`), and its
existing override mechanism — weak `cmtrace_com_tx`/`cmtrace_com_rx`/
`cmtrace_clear_caches`, link-order-based `cmtrace_timer_*` — maps closely
onto the new contract already, with no CMake-variable inversion to undo and
no pre-existing stale build state to untangle first. It gives the highest
learning value per unit of risk: it forces solving the two hardest general
problems (turning a "library others link" into a library that owns
`app_main`, and proving a genuinely generic BSP can replace a
project-specific one) without simultaneously fighting build-system
pluralism or a packaged-distribution pipeline.

**lean-ftl** Its release/dist model
(`get-lean-ftl`/`get-local-lean-ftl` → `lean-ftl-dist/{debug,minSizeRel}`)
is the closest existing prior art to lean-bsp's fetch mechanism, so porting
it feeds directly into hardening that part of the design. It also produces
a real, reusable `lean-bsp-nucleo-u5a5` — `crypto-benchmark-stm32u5` and
`lean-ftl-stm32u5` were confirmed by diffing their `.ioc`/linker/startup
files to be the *same physical Nucleo board* (`NUCLEO-U5A5ZJ-Q`), just
regenerated at two different CubeMX versions (6.16.1 vs 6.11.1) — so this
migration is the first concrete proof that one generic BSP can serve two
unrelated applications. Drop support for ch32v307.

**crypto-benchmark** It has the most CPUs, the most existing BSPs,
and the most build-system diversity (a plain STM32CubeMX-generated Makefile
for stm32u5, CMake + the Raspberry Pi Pico SDK for rp2350, a hand-written
Makefile over CMSIS-Toolbox packs for m5531). Despite that diversity, all
three BSPs consume the core the same way: they link the per-CPU static
library the core CMake emits
(`../crypto-benchmark/build/<cpu>/libcrypto-benchmark.a`, built with LTO
objects, so each BSP links with `-flto`). That maps directly onto
`[app.build]` (§5): the existing `buildit` + `link_ext.py` pair becomes the
application's build command, picking its per-CPU dependencies from
`LEAN_BSP_CPU` and its flags from `LEAN_BSP_CFLAGS` instead of its own
`target/<cpu>.cmake`. lean-benchmark's board hooks become one-line
application-side shims over `bsp_api.h` (`LBMK_get_cpu_timestamp` →
`bsp_cycle_count`, `LBMK_clear_caches` → `bsp_clear_caches`, the heap hooks →
`bsp_heap_usage_reset`/`bsp_heap_usage`), and `benchmark.py`'s
`find-uart` step becomes the `com_device` that `run` reports. By the time
this migration starts, `lean-bsp-nucleo-u5a5` already exists from the lean-ftl
migration, so most of the work is *consuming* an already-proven BSP rather
than a fresh board bring-up.

### What moves where, per repo

The general rule: CubeMX-generated HAL configuration, clock tree, pin mux,
CMSIS headers, the startup file, and the linker script's memory map are
board concerns and move into the new generic BSP. Anything that encodes
what the firmware actually *does* — test loops, benchmark harness calls,
which NVM regions get registered — becomes the restructured core repo's
`app_main`.

Concretely for `stm32u5` (the board shared across two projects): reusable
as-is into a single `lean-bsp-nucleo-u5a5` — the CMSIS/HAL/Nucleo `Drivers/`
tree, `startup_stm32u5a5xx.s`, the `.ld` memory map, `SystemClock_Config()`
and related bring-up, and — moved wholesale out of lean-ftl's
`target/stm32/stm32u5-accessors.c` — the STM32U5 flash-controller
erase/write sequence, since that's a silicon concern, not an application
one. Likewise moved out of `crypto-benchmark-stm32u5`: the ICACHE/DCACHE
invalidation in its `LBMK_clear_caches` (becoming `bsp_clear_caches`) and
the heap high-water-mark tracking in its `Core/Src/sysmem.c` (becoming
`bsp_heap_usage`). Staying application-side: crypto-benchmark's `hw_info[]` instrumentation
array, lean-ftl's `test_callbacks(); test_main();` harness call, and the
decision of which `lftl_ctx_t` function pointers get registered with what
region geometry.

Concretely for the `cm-trace-stm32f207` pilot: reusable as-is into
`lean-bsp-nucleo-f207` — CMSIS/HAL drivers, `startup_stm32f207xx.s`, the
linker script, `SystemClock_Config()`/peripheral init, the UART3-based
`__io_putchar`/`__io_getchar`, the LED on/off calls, the
`cmtrace_clear_caches` implementation (becoming `bsp_clear_caches`), and the TIM5-based
timer implementation that today overrides `dwt.c`'s DWT-based default by
link order (this becomes the BSP's cycle-counter primitive; the DWT-based
default itself is a reasonable candidate for a shared "if the BSP doesn't
provide one, fall back to ARM DWT" convenience in lean-bsp, worth revisiting
once the pilot is underway). Staying application-side: the actual trace-loop
test payload currently invoked from `main()`.

### Incremental path

Legacy core repos keep working during a transition window rather than being
rewritten in one step: cm-trace's existing `TARGET_CPU`-parameterized CMake
target keeps building unmodified for the old `cm-trace-stm32f207` BSP, while
a new `app_main`-based build target is added alongside it in the same repo.
New boards or projects adopt lean-bsp immediately, with no exceptions, even
before a given legacy migration is finished. Old BSP repos are retired
opportunistically, prioritized by which boards already have (or will soon
gain, per the order above) a generic-BSP replacement. Shared scaffolding files
(`gcc-arm-none-eabi.cmake`, `git_version.cmake`, and similar) are deleted
from a legacy repo only after that repo's app-lib build is validated via
lean-bsp's own tooling — never delete-then-fix.

### The cm-trace pilot: definition of done

1. `cm-trace` is restructured to expose `app_main()` (wrapping the
   `cmtrace_trace_loop()` payload that `../cm-trace-stm32f207/Core/Src/main.c`
   invokes today) and to declare its weak-symbol
   needs (`cmtrace_com_tx`/`cmtrace_com_rx`/`cmtrace_clear_caches`,
   `cmtrace_timer_*`) via `app.toml`. The old `libcmtrace` CMake target
   keeps building unmodified during the transition window.
2. `lean-bsp-nucleo-f207` is created as a project-agnostic repo: CubeMX
   bring-up, the modular `linker/` fragment set, the four-line `main()`
   glue, `bsp.toml` + `bsp_cli.py`, and implementations of the UART3, LED,
   TIM5-based cycle-counter, and cache-clear (`bsp_clear_caches`)
   primitives. It contains
   no mention of "cmtrace" anywhere in its source.
3. `cm-trace-stm32f207` (the old project-specific repo) is retired, replaced
   by `lean-bsp-nucleo-f207` plus the restructured cm-trace app-lib, built
   through the lean-bsp CLI.
4. **Cross-reuse proof:** the same, unmodified `lean-bsp-nucleo-f207` also
   builds successfully against `lean-app-hello-world`, once
   `lean-bsp-linux-host` and the hello-world app are far enough along for
   this to be meaningful. This is the concrete, falsifiable test that the
   BSP is genuinely generic rather than merely relocated: if any
   board-specific tweak were needed to make the second application build,
   the split was done wrong.
5. **Regression check:** the cm-trace app-lib built against
   `lean-bsp-nucleo-f207` reproduces the same cycle-count trace behavior as
   the legacy `cm-trace-stm32f207` — at minimum, a byte-for-byte comparison
   of UART output against a captured legacy-firmware baseline.
6. The scaffolding-duplication eliminated by this migration (file/line
   count of `lean-bsp-nucleo-f207` versus the sum of the old per-project BSP
   boilerplate it replaces) is written up here or in a migration log, as
   the quantifiable evidence for the drawback CLAUDE.md originally called
   out.

Where the pilot stands:

1. Done. cm-trace has an `app.toml` and one 59-line glue file,
   `lean-bsp/cmtrace_lean_bsp.c`, which maps its support functions onto
   `bsp_api.h` and runs `cmtrace_trace_loop` from `app_main`; it builds
   `libcmtrace` without `dwt.c`. The CMake build of `libcmtrace` is
   untouched. Its timer needs became contract 1.1's cycle timer (§1).
2. Done. `lean-bsp-nucleo-f207` offers the cycle timer on TIM5; its source
   does not mention cmtrace. The cycle counter stays the DWT, which counts
   the same core clock.
3. Done. `cm-trace-stm32f207` is archived; its README says it is
   deprecated and points to cm-trace's lean-bsp build on
   `lean-bsp-nucleo-f207`.
4. Done, on hardware: hello-world runs on the same `lean-bsp-nucleo-f207`.
5. Done, adapted. A byte-for-byte comparison of the UART stream cannot hold:
   it carries absolute addresses, which move with the link layout. The
   comparison is of the decoded traces instead: every column of
   `cmtrace-dump` but the address (opcode, cycles, first and last cycle,
   function, call events), for `umul64` with each operand size and `loop64`
   with 4, 32 and 256 iterations (up to 4,104 instructions, 4,628 cycles).
   They are identical. The baseline is the committed `cm-trace-stm32f207`
   with the committed cm-trace, its library built `-Og` like lean-bsp's
   build so both trace the same machine code; captures went through
   lean-hwbridge's `serial pty`.
6. The numbers. The legacy per-project BSP was 26 files and 4,016 lines,
   plus 846 driver files (63 MB). Under lean-bsp, cm-trace's own board
   support is 2 files and 80 lines (`app.toml` and the glue); the board
   support itself lives once, in `lean-bsp-nucleo-f207` (24 files and 3,779
   lines, plus 170 driver files, 8.8 MB), shared by every application that
   runs on that board, hello-world already among them.

Once this checklist is green, the same structure — repo restructure, generic
BSP repo, retirement of the old BSP, cross-app reuse proof, regression
check — repeats for `lean-ftl` (with `lean-bsp-nucleo-u5a5` as its milestone
board) and then `crypto-benchmark`.

## Critical files referenced by this design

- `README.md`, `CLAUDE.md` — the original proposal and problem statement.
- `../lean-app-hello-world/app_main.c`, `../lean-bsp-linux-host/` — existing
  placeholder siblings this design stays consistent with.
- `../cm-trace/libcmtrace/include/cmtrace.h`,
  `../cm-trace/libcmtrace/source/dwt.c` — the weak/link-order override
  precedent.
- `../cm-trace-stm32f207/Core/Src/main.c`,
  `../cm-trace-stm32f207/STM32F207XX_FLASH.ld` — the pilot's BSP shape and
  linker script to decompose.
- `../lean-ftl/liblean-ftl/include/lean-ftl.h`,
  `../lean-ftl/liblean-ftl/CMakeLists.txt`,
  `../lean-ftl/target/stm32/stm32u5-accessors.c` — the NVM function-pointer
  contract and the `LFTL_ACCESSORS` inversion to fix during the lean-ftl
  migration.
- `../crypto-benchmark/hardware-platforms/{stm32u5,rp2350,m5531}.py`,
  `../crypto-benchmark/benchmark.py` — the closest existing prior art for the
  per-BSP Python adapter contract.
- `../crypto-benchmark/setup_manifest.py`, `../crypto-benchmark/initial-setup`
  — the closest existing prior art for the registry and `fetch`.
- `../{cm-trace,crypto-benchmark,lean-ftl}/cmake/{generic_binary.cmake,git_version.cmake}`,
  plus `gcc-arm-none-eabi.cmake` (in `cmake/` for cm-trace and lean-ftl, in
  `target/` for crypto-benchmark) — near-identical shared scaffolding to fold
  into lean-bsp and the generic BSPs.
- `../lean-com/pyproject.toml`, `../lean-init/pyproject.toml` — the org's
  packaging convention mirrored by lean-bsp's own `pyproject.toml`.
