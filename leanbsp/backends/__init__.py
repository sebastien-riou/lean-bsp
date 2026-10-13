"""Optional helpers for BSP entry points (ARCHITECTURE.md, section 5).

The contract is the entry-point protocol, not these modules: a BSP may answer the verbs any way it
likes. lean-bsp runs a Python entry point with its own interpreter, so a BSP's bsp_cli.py can
import these, and then only describes its board:

- board: the four verbs, the build directory's state, and load/run through lean-hwbridge;
- gcc: a plain gcc build, the BSP's sources and the application linked with one linker script.
"""
