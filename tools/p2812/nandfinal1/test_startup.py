#!/usr/bin/env python3
"""Run actual MIPS reset/cache startup to board_init_f, with cache-op model.

Linux-only Unicorn. UART function is stubbed; cache instructions are recorded
and skipped, so no real tags/parity/coherency or VRX200 hardware is emulated.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import struct
import sys
import tempfile
import zipfile

from model_support import physical
WHEEL = Path('/inputs/unicorn.whl')
WHEEL_SHA = '9d6e6dea140560de4ebd8446661f7ef84a357d428c14a3ef09dacd306ec8c239'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    args = parser.parse_args()
    assert platform.system() == "Linux", "Native macOS Unicorn disabled"
    assert hashlib.sha256(WHEEL.read_bytes()).hexdigest() == WHEEL_SHA
    out = args.build
    symbols = {}
    for line in (out / "symbols.txt").read_text().splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16) & 0xffffffff
    with tempfile.TemporaryDirectory(prefix="p2812-cache-startup-") as temporary:
        with zipfile.ZipFile(WHEEL) as archive:
            assert all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in archive.namelist())
            archive.extractall(temporary)
        sys.path.insert(0, temporary)
        from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE
        import unicorn.mips_const as m
        mu = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_BIG_ENDIAN)
        mu.ctl_set_cpu_model(m.UC_CPU_MIPS32_34KF)
        mu.mem_map(0, 0x08000000)
        raw = (out / "u-boot.bin").read_bytes()
        mu.mem_write(0x00100000, raw)
        sentinel = bytes(range(256)) * 4
        mu.mem_write(0x02000000, sentinel)
        # Read the model's flags and supply the cache geometry measured in
        # the modem's Linux log: 32 KiB, four ways, 32-byte lines, I and D.
        mu.mem_write(0x03ff0000, struct.pack(">III", 0x40028001, 0x03e00008, 0))
        mu.reg_write(m.UC_MIPS_REG_RA, 0xa3ff1000)
        mu.emu_start(0xa3ff0000, 0xa3ff1000, count=20)
        native_config1 = mu.reg_read(m.UC_MIPS_REG_V0)
        geometry_mask = sum(7 << shift for shift in (22, 19, 16, 13, 10, 7))
        geometry = (2 << 22) | (4 << 19) | (3 << 16) | (2 << 13) | (4 << 10) | (3 << 7)
        model_config1 = (native_config1 & ~geometry_mask) | geometry
        registers = [getattr(m, f"UC_MIPS_REG_{index}") for index in range(32)]
        cache_ops, config_writes, uart_calls = [], [], []
        reached = False
        def hook(uc, pc, size, _):
            nonlocal reached
            if pc == symbols["board_init_f"]:
                reached = True
                uc.emu_stop()
                return
            if pc == symbols["debug_uart_init"]:
                uart_calls.append(hex(pc))
                uc.reg_write(m.UC_MIPS_REG_PC, uc.reg_read(m.UC_MIPS_REG_RA))
                return
            instruction = struct.unpack(">I", uc.mem_read(physical(pc), 4))[0]
            opcode = instruction >> 26
            if opcode == 0x2f:
                base, op = (instruction >> 21) & 31, (instruction >> 16) & 31
                offset = instruction & 0xffff
                if offset & 0x8000:
                    offset -= 0x10000
                address = (uc.reg_read(registers[base]) + offset) & 0xffffffff
                cache_ops.append({"op": op, "address": address})
                uc.reg_write(m.UC_MIPS_REG_PC, pc + 4)
            elif opcode == 0x10 and ((instruction >> 21) & 31) == 4:
                rd, sel = (instruction >> 11) & 31, instruction & 7
                if rd == 16 and sel == 0:
                    rt = (instruction >> 16) & 31
                    config_writes.append(uc.reg_read(registers[rt]) & 0xffffffff)
            elif opcode == 0x10 and ((instruction >> 21) & 31) == 0:
                rd, sel = (instruction >> 11) & 31, instruction & 7
                if rd == 15 and sel == 1:
                    # Unicorn's generic EBase CPU-ID is unsuitable for this
                    # bootstrap-CPU entry. Model the modem's CPU0 selection;
                    # the actual startup branch remains executed unchanged.
                    rt = (instruction >> 16) & 31
                    uc.reg_write(registers[rt], 0x80000000)
                    uc.reg_write(m.UC_MIPS_REG_PC, pc + 4)
                elif rd == 16 and sel == 1:
                    rt = (instruction >> 16) & 31
                    uc.reg_write(registers[rt], model_config1)
                    uc.reg_write(m.UC_MIPS_REG_PC, pc + 4)
        mu.hook_add(UC_HOOK_CODE, hook)
        mu.emu_start(symbols["_start"], 0xa3ff1000, count=200000)
        assert reached and uart_calls, {
            "message": "Actual reset/cache path did not reach board_init_f",
            "pc": hex(mu.reg_read(m.UC_MIPS_REG_PC)), "uart_calls": uart_calls,
            "cache_ops": len(cache_ops), "config_writes": [hex(v) for v in config_writes],
            "board_init_f": hex(symbols["board_init_f"]),
        }
        assert config_writes and [value & 7 for value in config_writes] == [2, 3]
        assert cache_ops and all(0x80000000 <= item["address"] < 0x80020000 for item in cache_ops)
        operations = Counter(item["op"] for item in cache_ops)
        assert operations == {8: 1024, 9: 1024}, operations
        assert bytes(mu.mem_read(0x02000000, len(sentinel))) == sentinel
        stack = mu.reg_read(m.UC_MIPS_REG_SP)
        assert 0xa03f0000 <= stack <= 0xa0400000
        # Read the emulator's actual CP0 Config0 through MIPS mfc0, rather
        # than relying solely on the intercepted write argument.
        stub = struct.pack(">III", 0x40028000, 0x03e00008, 0)
        mu.mem_write(0x03ff0000, stub)
        mu.reg_write(m.UC_MIPS_REG_RA, 0xa3ff1000)
        mu.emu_start(0xa3ff0000, 0xa3ff1000, count=20)
        config0 = mu.reg_read(m.UC_MIPS_REG_V0)
        assert config0 & 7 == 3
        result = {
            "passed": True, "hardware_access": False, "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "actual_reset_to_board_init_f": True, "cache_cca_write_sequence": [v & 7 for v in config_writes],
            "emulator_config0": hex(config0), "emulator_k0_cca": config0 & 7,
            "cache_tag_operations_by_opcode": dict(operations), "stack_at_board_init_f": hex(stack),
            "model_config1_cache_geometry": hex(model_config1),
            "preloaded_kernel_ram_sentinel_preserved": True,
            "models": ["cache instructions recorded/skipped", "debug_uart_init stub", "EBase CPU-ID=0",
                       "Config1 I/D geometry: 32 KiB, four ways, 32-byte lines, from actual Linux log"],
            "limits": ["No cache tag/parity/coherency or VRX200/DDR hardware is emulated",
                       "Stops before board_init_f; not a complete U-Boot or Linux boot test"],
        }
        (out / "startup-cache-tests.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
