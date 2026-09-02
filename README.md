# lean-bsp

Framework to leverage generic BSPs in any project.

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


## related repostories
They are cloned at the same level as this one.

- [`lean-bsp-linux-host`](https://github.com/sebastien-riou/lean-bsp-linux-host): the linux-host BSP
- [`lean-app-hello-world`](https://github.com/sebastien-riou/lean-app-hello-world): minimal example of a cross platform project using this framework