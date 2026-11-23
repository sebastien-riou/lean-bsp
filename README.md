# lean-bsp

Framework to leverage generic BSPs in any project.

| | |
|---|---|
| BSPs | [linux-host](https://github.com/sebastien-riou/lean-bsp-linux-host), [nucleo-f207](https://github.com/sebastien-riou/lean-bsp-nucleo-f207), [nucleo-l552](https://github.com/sebastien-riou/lean-bsp-nucleo-l552), [nucleo-u5a5](https://github.com/sebastien-riou/lean-bsp-nucleo-u5a5), [numaker-m5531](https://github.com/sebastien-riou/lean-bsp-numaker-m5531), [pico2](https://github.com/sebastien-riou/lean-bsp-pico2), [mpfs-disco-kit](https://github.com/sebastien-riou/lean-bsp-mpfs-disco-kit) |
| Applications | [hello-world](https://github.com/sebastien-riou/lean-app-hello-world), [cm-trace](https://github.com/sebastien-riou/cm-trace), [lean-ftl](https://github.com/sebastien-riou/lean-ftl) |

## Concept

- Application is built as a library with few unimplemented / weak symbols to handle hardware specific things.
- Each generic BSP provides 
    - implementation for commonly needed functions:
        - UART RX/TX
        - turn on/off an LED
        - get button state
        - Get CPU cycle
        - get CPU frequency
        - Read/erase/write flash
        - ...
    - main function just call to app_main (several other app_* hooks are defined)
    - A default, modular, linker script: it is made up several files with standard names such that an application can make a custom linker script which include them.
    - a python script which is capable of:
        - build
        - load
        - run
        - enumerate the capabilities of the BSP

This project provides a python package including a CLI to build an application given such "application lib" + "generic BSP". It also enumerate the known BSP repositories and can fetch them.

## Advantages

- BSP can be reused over several projects and can be based on Silicon vendors code generator. 
- The application can be opened with Silicon vendor tools and debugged as such.
- Application projects are cross-platform from day 1 (there is a BSP for 'linux-host')

## Usage

Install from a clone (Python 3.11 or later, no runtime dependencies) with [pipx](https://pipx.pypa.io); this
provides both `lean-bsp` and its short form `lbsp`, and, being editable, follows the clone as it changes:

````
pipx install --editable .
````

pipx keeps lean-bsp in its own virtual environment, which is what a system Python that refuses `pip install`
(PEP 668: "externally-managed-environment", as on Ubuntu 24.04) expects. Inside a virtual environment of your own,
`pip install -e .` does the same. lean-bsp runs the BSPs' Python entry points with its own interpreter, so they
find its helpers (`leanbsp.backends`) either way.

Then, from inside an application repository (one with an `app.toml`), with its BSPs cloned next to it:

````
lbsp build-load-run                      # BSP from app.toml's default_bsp, e.g. linux-host
lbsp build-load-run --bsp nucleo-l552    # same application, real hardware
lbsp list-bsps                           # known BSPs and which ones are cloned
lbsp check --bsp nucleo-l552             # does this BSP offer every primitive the application needs?
````

`lbsp --help` and `lbsp <command> --help` list everything. Without installing, `python3 -m leanbsp` runs the same
CLI from a clone of this repository (with the clone on `PYTHONPATH`).

The design, including the BSP/application contract (`bsp_api.h`) and the BSP entry-point protocol, is in
[ARCHITECTURE.md](ARCHITECTURE.md).

Run the tests with `python3 -m pytest test`.

## Status

Phase 1 of the migration plan (ARCHITECTURE.md §6) is done: hello-world runs end to end on `linux-host` and
on all six board targets (through [lean-hwbridge](https://github.com/sebastien-riou/lean-hwbridge) for the
boards), and debugs in STM32CubeIDE on `nucleo-f207`. The first legacy project, cm-trace, now builds and traces
as a lean-bsp application on `nucleo-f207`, through contract 1.1's cycle timer, with traces identical to its old
board project's. Not implemented yet: `[app.build]` applications and `lean-bsp registry add`.

## related repostories
They are cloned at the same level as this one.

- [`lean-bsp-linux-host`](https://github.com/sebastien-riou/lean-bsp-linux-host): the linux-host BSP
- [`lean-app-hello-world`](https://github.com/sebastien-riou/lean-app-hello-world): minimal example of a cross platform project using this framework, and the report every new BSP is checked with
- Board BSPs: [`lean-bsp-nucleo-f207`](https://github.com/sebastien-riou/lean-bsp-nucleo-f207),
  [`lean-bsp-nucleo-l552`](https://github.com/sebastien-riou/lean-bsp-nucleo-l552),
  [`lean-bsp-nucleo-u5a5`](https://github.com/sebastien-riou/lean-bsp-nucleo-u5a5),
  [`lean-bsp-numaker-m5531`](https://github.com/sebastien-riou/lean-bsp-numaker-m5531),
  [`lean-bsp-pico2`](https://github.com/sebastien-riou/lean-bsp-pico2),
  [`lean-bsp-mpfs-disco-kit`](https://github.com/sebastien-riou/lean-bsp-mpfs-disco-kit)
- [`lean-hwbridge`](https://github.com/sebastien-riou/lean-hwbridge): how the board BSPs' `load` and `run`
  reach the boards' power, serial ports and debug probes, from a sandboxed session or another machine