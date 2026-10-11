# Notes for Claude

## The problem
There are three projects, each made of several git repositories that sit next to this one as siblings:
- `cm-trace`: core `../cm-trace`, BSP `../cm-trace-stm32f207`
- `crypto-benchmark`: core `../crypto-benchmark`, BSPs `../crypto-benchmark-m5531`, `../crypto-benchmark-rp2350`, `../crypto-benchmark-stm32u5`
- `lean-ftl`: core `../lean-ftl`, BSPs `../lean-ftl-stm32l5`, `../lean-ftl-stm32u5` (its ch32v307 BSP is not cloned: support for that board is being dropped)

They are all following the same pattern:
- A generic repo which contain the core of the project
    - It can build a library for several CPU types
    - The library leaves all board specific functions undefined
- Several 'BSP' repos, one for each desired target board
    - Define all board specific functions
    - Contain the startup file and main function

This pattern has the advantage of scaling well no matter the number of target boards.
A drawback is that each 'BSP' repo is specific to the project and such repo is marginally different from one project to another.
Another drawback is that each project core repo reproduce all the scaffolding for building the library on many CPU types, there is clearly scope for code sharing.

## The proposed approach
It is outlined in README.md

