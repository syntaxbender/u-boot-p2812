"""Relocation and compiler-derived structure offsets, without loading Unicorn."""
import struct

FIELDS = [
    'gd_size', 'gd_bd', 'gd_ram_size', 'gd_flags', 'gd_dram_start', 'gd_dram_size',
    'gd_stack', 'gd_initial_reloc', 'gd_ram_top', 'gd_fdt', 'gd_flag_reloc',
    'bd_size', 'bd_params', 'images_size', 'images_os', 'images_ep', 'images_state',
    'images_ft_len', 'bmi_size', 'bmi_images', 'os_start', 'os_end', 'os_image_start',
    'os_image_len', 'os_load', 'os_comp', 'os_type', 'os_os', 'os_arch',
    'flag_load', 'flag_prep', 'flag_go', 'flag_start', 'mtd_size', 'mtd_bytes',
    'mtd_flags', 'mtd_page', 'mtd_oob', 'mtd_erase', 'mtd_corrected',
    'lmb_available', 'lmb_used', 'alist_data', 'alist_count', 'region_size',
    'region_base', 'region_bytes', 'region_flags', 'phys_bytes', 'phys_size_bytes',
    'cmd_size', 'cmd_name', 'cmd_func',
]
TEXT, RELOC = 0xa0100000, 0xa7f00000
GD, BD, MTD = 0xa3100000, 0xa3110000, 0xa3120000
PARAMS, SENTINEL = 0xa3300000, 0xa33ff000


def physical(addr):
    return addr & 0x1fffffff if 0x80000000 <= addr < 0xc0000000 else addr


def symbols(path):
    return {f[2]: int(f[0], 16) & 0xffffffff for row in path.read_text().splitlines()
            if len(f := row.split()) == 3}


def prepare(main):
    syms = symbols(main / 'symbols.txt')
    data = (main / 'linuxrun-layout.bin').read_bytes()
    count_fields = min(len(FIELDS), len(data) // 4)
    facts = dict(zip(FIELDS, struct.unpack_from('>' + 'I' * count_fields, data)))
    raw = (main / 'u-boot.bin').read_bytes()
    end = syms['__image_copy_end'] - TEXT
    relocated, pos, offset, count = bytearray(raw[:end]), end, 0, 0
    def number():
        nonlocal pos
        value, shift = 0, 0
        while True:
            byte = raw[pos]; pos += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
            shift += 7
    while True:
        kind = number()
        if kind == 0xbeef7531:
            break
        offset += number() * 4
        value = struct.unpack_from('>I', relocated, offset)[0]
        if kind == 2:
            value += RELOC - TEXT
        elif kind == 4:
            value = (value & 0xfc000000) | ((value + ((RELOC - TEXT) >> 2)) & 0x3ffffff)
        elif kind == 5:
            value += (RELOC - TEXT) >> 16
        else:
            raise RuntimeError('Unknown MIPS relocation ' + str(kind))
        struct.pack_into('>I', relocated, offset, value & 0xffffffff)
        count += 1
    if syms['__bss_end'] - TEXT >= 0x100000:
        raise RuntimeError('Model relocation window too small')
    return syms, facts, bytes(relocated), count
