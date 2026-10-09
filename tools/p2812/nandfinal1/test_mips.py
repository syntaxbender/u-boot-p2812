#!/usr/bin/env python3
"""Run actual relocated verify/START/LOADOS/PREP/GO and the real LMB allocator.

Only the kernel entry is intercepted. Console, heap, IRQ/cache and optional
NAND hardware services are modeled; no Linux instruction or device executes.
"""
import argparse
import hashlib
import json
import lzma
from pathlib import Path
import platform
import struct
import sys
import tempfile
import zipfile
import zlib
from model_support import prepare, physical, TEXT, RELOC, GD, BD, MTD, PARAMS, SENTINEL

WHEEL_SHA = '9d6e6dea140560de4ebd8446661f7ef84a357d428c14a3ef09dacd306ec8c239'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def repaired(image):
    result = bytearray(image)
    struct.pack_into('>I', result, 24, zlib.crc32(result[64:]))
    result[4:8] = bytes(4)
    struct.pack_into('>I', result, 4, zlib.crc32(result[:64]))
    return bytes(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('build', type=Path)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, default=Path('/inputs/unicorn.whl'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--negative-inputs-only', action='store_true')
    args = parser.parse_args()
    require(platform.system() == 'Linux', 'Native macOS Unicorn is prohibited')
    require(hashlib.sha256(args.wheel.read_bytes()).hexdigest() == WHEEL_SHA, 'Wheel differs')
    syms, f, relocated, relocations = prepare(args.build / 'main')
    require(f['phys_bytes'] == f['phys_size_bytes'] == 4, 'Model ABI requires 32-bit LMB addresses')
    def address(name):
        return syms[name] + RELOC - TEXT
    cases = []
    with tempfile.TemporaryDirectory(prefix='nandfinal1-unicorn-') as tmp:
        with zipfile.ZipFile(args.wheel) as archive:
            require(all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist()), 'Unsafe wheel')
            archive.extractall(tmp)
        sys.path.insert(0, tmp)
        from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE
        import unicorn.mips_const as m

        def run(name, image, source='ram', mode='ok', cancel_at=0, argc=1, bad_first=False, repeat=False):
            if args.negative_inputs_only and not name.startswith(('ram_bad_', 'ram_wrong_', 'ram_reject_')):
                return
            image = bytes(image)
            expected = None
            if mode not in {'bad_property', 'bad_unknown_size', 'oversize_output', 'short_stream'}:
                try:
                    expected = lzma.decompress(image[64:], format=lzma.FORMAT_ALONE)
                except lzma.LZMAError:
                    require(mode != 'ok', 'Invalid successful fixture')
            mu = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_BIG_ENDIAN)
            mu.ctl_set_cpu_model(m.UC_CPU_MIPS32_34KF)
            mu.reg_write(m.UC_MIPS_REG_CP0_STATUS, 0x400001)
            for start, length in [(0, 0x1100000), (0x2000000, 0x1000000),
                                  (0x3000000, 0x500000), (physical(RELOC), 0x100000)]:
                mu.mem_map(start, length)
            mu.mem_write(physical(RELOC), relocated)
            mu.reg_write(m.UC_MIPS_REG_K0, GD)
            def put32(addr, value):
                mu.mem_write(physical(addr), struct.pack('>I', value & 0xffffffff))
            def get32(addr):
                return struct.unpack('>I', mu.mem_read(physical(addr), 4))[0]
            def text(addr):
                return bytes(mu.mem_read(physical(addr), 1024)).split(b'\0', 1)[0].decode('latin1')
            def result(value=0):
                mu.reg_write(m.UC_MIPS_REG_V0, value & 0xffffffff)
                mu.reg_write(m.UC_MIPS_REG_PC, mu.reg_read(m.UC_MIPS_REG_RA))
            def regions():
                base = address('lmb') + f['lmb_used']
                data = get32(base + f['alist_data'])
                count = struct.unpack('>H', mu.mem_read(physical(base + f['alist_count']), 2))[0]
                return [[get32(data + i * f['region_size'] + f[k]) for k in
                         ['region_base', 'region_bytes', 'region_flags']] for i in range(count)]
            put32(GD + f['gd_bd'], BD)
            put32(GD + f['gd_ram_size'], 0x4000000 if mode == 'small_ram' else 0x8000000)
            put32(GD + f['gd_flags'], f['gd_flag_reloc'])
            put32(GD + f['gd_dram_start'], 0xa0000000)
            put32(GD + f['gd_dram_size'], 0x8000000)
            put32(GD + f['gd_stack'], 0xa7dbd000)
            put32(GD + f['gd_initial_reloc'], 0xa8000000)
            put32(GD + f['gd_ram_top'], 0xa8000000)
            put32(BD + f['bd_params'], PARAMS)
            mu.mem_write(physical(MTD + f['mtd_bytes']), struct.pack('>Q', 0x8000000))
            for field, value in [('mtd_flags', 0), ('mtd_page', 2048), ('mtd_oob', 64), ('mtd_erase', 0x20000)]:
                put32(MTD + f[field], value)
            storage = (b'\xff' * 0x20000 if bad_first else b'') + image
            storage = storage.ljust(0x500000, b'\xff')
            mu.mem_write(0x2000000, image)
            output_sentinel = b'\xa5' * 256
            mu.mem_write(0x2000, output_sentinel)
            events = {'bootm_states': [], 'fdt_selectors': [], 'allocations': [],
                      'reservation_records': [], 'lmb_free_calls': [], 'cache_flushes': [],
                      'nand_reads': [], 'nand_marker_queries': [], 'console': []}
            state = {'heap': 0xa3010000, 'entered': False, 'returned': False,
                     'irq_enabled': True, 'cancellations': 0, 'command_active': False,
                     'mode': mode, 'kernel_registers': None}
            images = address('images')
            def os_field(key):
                return images + f['images_os'] + f['os_' + key]
            def hook(uc, pc, size, _):
                a0, a1, a2, a3 = [uc.reg_read(reg) for reg in [m.UC_MIPS_REG_A0, m.UC_MIPS_REG_A1, m.UC_MIPS_REG_A2, m.UC_MIPS_REG_A3]]
                current = state['mode']
                if pc == SENTINEL:
                    state['returned'] = True; uc.emu_stop()
                elif pc == 0x80002000:
                    require(state['command_active'] and not state['irq_enabled'], 'Kernel entry IRQ/context wrong')
                    require(expected is not None and bytes(uc.mem_read(0x2000, len(expected))) == expected, 'Decoded kernel differs')
                    require([0xa0002000, len(expected), 0] in regions(), 'Output reservation lost before Linux')
                    state['kernel_registers'] = {n: uc.reg_read(reg) for n, reg in
                        [('a0', m.UC_MIPS_REG_A0), ('a1', m.UC_MIPS_REG_A1),
                         ('a2', m.UC_MIPS_REG_A2), ('a3', m.UC_MIPS_REG_A3)]}
                    require(state['kernel_registers']['a0'] == 1 and state['kernel_registers']['a1'] == PARAMS,
                            'Legacy Linux argc/argv differs')
                    env = state['kernel_registers']['a2']
                    require(any(text(get32(env + i * 4)) == 'memsize=128'
                                for i in range(8) if get32(env + i * 4)), 'Missing legacy memory size')
                    require(state['kernel_registers']['a3'] == 0, 'Unexpected Linux extra arg')
                    state['entered'] = True; uc.emu_stop()
                elif pc in [address(n) for n in ['do_reset', 'reset_cpu'] if n in syms]:
                    raise RuntimeError('Unexpected reset in ' + name)
                elif pc in [address(n) for n in ['malloc', 'calloc']]:
                    count = a0 * a1 if pc == address('calloc') else a0
                    pointer = state['heap']; state['heap'] = (pointer + count + 15) & ~15
                    require(state['heap'] < GD, 'Modeled heap exhausted')
                    mu.mem_write(physical(pointer), bytes(count))
                    events['allocations'].append(count); result(pointer)
                elif pc == address('free'):
                    result()
                elif pc == address('disable_interrupts'):
                    old = state['irq_enabled']; state['irq_enabled'] = False; result(int(old))
                elif pc == address('enable_interrupts'):
                    state['irq_enabled'] = True; result()
                elif pc == address('bootm_final'):
                    result()
                elif pc == address('flush_cache'):
                    events['cache_flushes'].append([a0, a1]); result()
                elif pc in [address(n) for n in ['printf', 'puts']]:
                    events['console'].append(text(a0)); result()
                elif pc == address('env_get'):
                    result(0)
                elif pc == address('ctrlc'):
                    state['cancellations'] += 1
                    result(int(state['cancellations'] == cancel_at))
                elif pc in [address(n) for n in ['crc32', 'crc32_wd']]:
                    require(a2 <= 0x1000000, 'Unbounded CRC read')
                    result(zlib.crc32(bytes(uc.mem_read(physical(a1), a2)), a0))
                elif pc == address('bootm_run_states') and state['command_active']:
                    events['bootm_states'].append(a1)
                    if a1 == f['flag_go'] and current in ['go_return', 'cleanup_failure']:
                        result(0)  # Explicit failure injection; not a successful kernel execution.
                elif pc == address('boot_get_fdt'):
                    events['fdt_selectors'].append(text(a1) if a1 else None)
                elif pc == address('do_bootm_linux') and a0 == f['flag_prep'] and current == 'prep_failure':
                    result(-12)
                elif pc == address('do_bootm_linux') and a0 == f['flag_prep'] and current == 'prep_changed_entry':
                    put32(images + f['images_ep'], 0x80003000)
                elif pc == address('p2812_linux_lmb_record'):
                    events['reservation_records'].append([a0, a1])
                    if current == 'postload_changed_entry':
                        put32(images + f['images_ep'], 0x80003000)
                    if current == 'postload_changed_length':
                        put32(os_field('image_len'), a1 + 1)
                elif pc == address('lmb_alloc_mem') and state['command_active']:
                    if current == 'load_allocation_failure':
                        result(-17)  # Actual allocator otherwise runs unchanged.
                elif pc == address('lmb_free'):
                    events['lmb_free_calls'].append([a0, a1, a2])
                    if current == 'cleanup_failure':
                        result(-5)  # Keep the real reservation, then test blocking.
                elif pc == address('ltq_nand_ro_probe'):
                    result()
                elif pc == address('ltq_nand_ro_mtd'):
                    result(MTD)
                elif pc == address('ltq_nand_ro_isbad'):
                    require(0x60000 <= a0 < 0x560000 and a0 % 0x20000 == 0, 'NAND marker bounds')
                    events['nand_marker_queries'].append(a0); result(int(bad_first and a0 == 0x60000))
                elif pc == address('ltq_nand_ro_read_page'):
                    require(a1 == 0 and 0x60000 <= a0 <= 0x55f800 and a0 % 2048 == 0, 'NAND read bounds/mode')
                    events['nand_reads'].append(a0)
                    if current == 'nand_ecc_failure':
                        result(-74)
                    else:
                        start = a0 - 0x60000
                        uc.mem_write(physical(a2), storage[start:start + 2048])
                        uc.mem_write(physical(a3), b'\xff' * 64); result()
            hooked = ['malloc', 'calloc', 'free', 'disable_interrupts', 'enable_interrupts',
                      'bootm_final', 'flush_cache', 'printf', 'puts', 'env_get', 'ctrlc',
                      'crc32', 'crc32_wd', 'bootm_run_states', 'boot_get_fdt',
                      'do_bootm_linux', 'p2812_linux_lmb_record', 'lmb_alloc_mem',
                      'lmb_free', 'ltq_nand_ro_probe', 'ltq_nand_ro_mtd', 'ltq_nand_ro_isbad',
                      'ltq_nand_ro_read_page', 'do_reset', 'reset_cpu']
            for pc in {address(n) for n in hooked if n in syms} | {SENTINEL, 0x80002000}:
                mu.hook_add(UC_HOOK_CODE, hook, begin=pc, end=pc)
            def call(symbol, arguments=(), entry=None):
                state['returned'] = False
                mu.reg_write(m.UC_MIPS_REG_SP, 0xa33fe000)
                mu.reg_write(m.UC_MIPS_REG_RA, SENTINEL)
                for reg, value in zip([m.UC_MIPS_REG_A0, m.UC_MIPS_REG_A1, m.UC_MIPS_REG_A2, m.UC_MIPS_REG_A3], arguments):
                    mu.reg_write(reg, value)
                mu.emu_start(entry if entry is not None else address(symbol), SENTINEL + 4,
                             timeout=240000000, count=3000000000)
                require(state['entered'] or state['returned'], 'Model did not reach boundary: ' + name)
                return mu.reg_read(m.UC_MIPS_REG_V0)
            # Set the modeled CPU's K0 CCA via actual privileged instructions.
            probe = [0x24080002 if mode == 'wrong_cache' else 0x24080003,
                     0x40888000, 0x000000c0, 0x03e00008, 0]
            mu.mem_write(0x34f0000, struct.pack('>5I', *probe))
            call('', entry=0xa34f0000)
            require(call('lmb_init') == 0, 'Real LMB init failed')
            baseline_regions = regions()
            if mode == 'reserved_output':
                put32(0xa34f0100, 0xa0002000)
                # lmb_alloc_mem(type, u64 align, addr*, size, flags) uses stack slots.
                sp = 0xa33fe000
                put32(sp + 16, 0xa34f0100); put32(sp + 20, 256); put32(sp + 24, 4)
                require(call('lmb_alloc_mem', [1, 0, 0, 0]) == 0, 'Foreign reservation setup failed')
            state['command_active'] = True
            ret = call('do_nandbootrun' if source == 'nand' else 'do_rambootrun', [0, 0, argc, 0])
            entered_first = state['entered']
            success = mode == 'ok' and not cancel_at and argc == 1
            require(entered_first == success, 'Wrong Linux entry result: ' + name)
            if success:
                require(events['bootm_states'] == [f[k] for k in ['flag_start', 'flag_load', 'flag_prep', 'flag_go']],
                        'Real bootm states differ')
                require(events['fdt_selectors'] == [None], 'External FDT selector changed')
                require(not events['lmb_free_calls'], 'Output freed before Linux')
            else:
                require(ret == (0xffffffff if argc != 1 else 1), 'Error did not return to console')
                if argc == 1:
                    require(bytes(mu.mem_read(0x2000000, 64)) == bytes(64), 'Input header not invalidated')
                    require(get32(images + f['images_state']) == f['flag_go'], 'Stale bootm state')
                if events['reservation_records'] and mode != 'cleanup_failure':
                    require(regions() == baseline_regions, 'Owned reservation leaked on failure')
                if not events['reservation_records']:
                    require(not events['lmb_free_calls'], 'Freed an unowned reservation')
                if not events['reservation_records'] and mode != 'load_allocation_failure':
                    require(bytes(mu.mem_read(0x2000, 256)) == output_sentinel or f['flag_load'] in events['bootm_states'],
                            'Output touched before LOADOS')
                require(state['irq_enabled'], 'Interrupt state not restored on error')
            if source == 'ram':
                require(not events['nand_reads'] and not events['nand_marker_queries'], 'RAM path touched NAND')
            if mode == 'cleanup_failure':
                before = [len(events[k]) for k in ['bootm_states', 'nand_reads', 'lmb_free_calls']]
                mu.mem_write(0x2000000, image)
                require(call('do_rambootrun', [0, 0, 1, 0]) == 1, 'Retained reservation did not block')
                require(before == [len(events[k]) for k in ['bootm_states', 'nand_reads', 'lmb_free_calls']],
                        'Blocked command retried allocation/release')
            if repeat:
                state.update(mode='ok', cancellations=0)
                cancel_at = 0
                mu.mem_write(0x2000000, image)
                call('do_rambootrun', [0, 0, 1, 0])
                require(state['entered'], 'Repeat after cleanup failed')
            row = {'case': name, 'passed': True, 'source': source,
                   'entered_kernel_first_attempt': entered_first,
                   'entered_kernel_after_retry': state['entered'], 'kernel_instructions_executed': 0,
                   'fault_injection': mode, 'cancel_at': cancel_at,
                   'real_lmb_allocator': True, 'real_bootm_states': events['bootm_states'],
                   'reservation_records': events['reservation_records'], 'releases': events['lmb_free_calls'],
                   'nand_pages_read': len(events['nand_reads']),
                   'kernel_registers': state['kernel_registers'],
                   'decoded_sha256': hashlib.sha256(expected).hexdigest() if expected is not None and state['entered'] else None}
            cases.append(row); print('PASS: ' + name, flush=True)

        tiny = (args.fixtures / 'tiny.uImage').read_bytes()
        for label in ['tiny', 'official', 'diagnostic']:
            run('ram_' + label + '_actual_kernel_boundary', (args.fixtures / (label + '.uImage')).read_bytes())
        official = (args.fixtures / 'official.uImage').read_bytes()
        run('nand_official_actual_reader_bootm_and_lmb', official, source='nand')
        run('nand_official_skip_first_bad_block', official, source='nand', bad_first=True)
        run('nand_uncorrectable_read_stops', tiny, source='nand', mode='nand_ecc_failure')
        for mode in ['wrong_cache', 'small_ram', 'reserved_output', 'load_allocation_failure',
                     'postload_changed_entry', 'postload_changed_length', 'prep_failure',
                     'prep_changed_entry', 'go_return', 'cleanup_failure']:
            run(mode, tiny, mode=mode)
        for cancelled in range(1, 6):
            run('cancel_at_stage_' + str(cancelled), tiny, cancel_at=cancelled)
        run('repeat_after_prep_failure_releases_real_lmb', tiny, mode='prep_failure', repeat=True)
        run('wrong_argc_before_access', tiny, argc=2)
        data = bytearray(tiny); data[4] ^= 1
        run('ram_bad_header_crc', data, mode='bad_header')
        data = bytearray(tiny); data[-1] ^= 1
        run('ram_bad_data_crc', data, mode='bad_data')
        data = bytearray(tiny); struct.pack_into('>I', data, 20, 0x80003000)
        run('ram_wrong_entry', repaired(data), mode='bad_entry')
        for label, change in [
            ('bad_property', lambda b: b.__setitem__(64, 255)),
            ('bad_unknown_size', lambda b: b.__setitem__(slice(69, 77), b'\xff' * 8)),
            ('oversize_output', lambda b: b.__setitem__(slice(69, 77), struct.pack('<Q', 0x1000001))),
            ('large_dictionary', lambda b: b.__setitem__(slice(65, 69), struct.pack('<I', 0x4000000))),
            ('oversize_input', lambda b: struct.pack_into('>I', b, 12, 0xffffffff)),
            ('decoded_size_mismatch', lambda b: b.__setitem__(slice(69, 77), struct.pack('<Q', 4097))),
        ]:
            data = bytearray(tiny); change(data)
            run('ram_reject_' + label, repaired(data), mode=label)
        report = {'passed': True, 'full_suite': not args.negative_inputs_only,
                  'cases_passed': len(cases), 'cases': cases,
                  'relocation_count': relocations, 'source': 'actual linked NANDFINAL1 main',
                  'hardware_access': False, 'linux_instructions_executed': 0,
                  'bootm_and_lmb_executed_as_linked': True,
                  'modeled_services': ['heap', 'console/environment', 'IRQ/cache physics', 'NAND MMIO'],
                  'negative_injections': ['allocation failure', 'Linux PREP service failure',
                                          'post-load/PREP metadata changes', 'GO service return', 'LMB release failure'],
                  'limitations': ['No full board startup', 'No physical NAND timing', 'No Linux execution',
                                 'Unexpected actual kernel returns are outside its noreturn ABI']}
        (args.output or (args.build / 'linuxrun-mips-tests.json')).write_text(json.dumps(report, indent=2) + '\n')
        print('PASS: %d actual MIPS Linux handoff cases, real LMB; zero kernel instructions' % len(cases))


if __name__ == '__main__':
    main()
