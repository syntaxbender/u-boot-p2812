#!/usr/bin/env python3
"""Generate and verify the source-only research baseline; never access flash."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import zlib

KIT = Path(__file__).resolve().parent
LOCK = json.loads((KIT / 'baseline.json').read_text())


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    with path.open('xb') as stream:
        stream.write(data)


def package_main(out):
    raw = (out / 'main/u-boot.bin').read_bytes()
    require(digest(raw) == LOCK['expected_outputs']['package/u-boot.bin'],
            'Main differs from the validated baseline')
    path = out / 'package/u-boot.bin'
    write(path, raw)
    path.chmod(0o644)
    epoch = LOCK['source_date_epoch']
    os.utime(path, (epoch, epoch))
    with path.open('rb') as stream:
        packed = subprocess.check_output(['lzop', '-9', '-c'], stdin=stream)
    require(subprocess.check_output(['lzop', '-d', '-c'], input=packed) == raw,
            'LZO round trip failed')
    header = struct.pack('>7I4B32s', 0x27051956, 0, epoch, len(packed),
                         0xa0100000, 0xa0100000, zlib.crc32(packed),
                         17, 5, 5, 4, b'U-Boot 2026.10 P2812 NANDBOOT2')
    header = header[:4] + struct.pack('>I', zlib.crc32(header)) + header[8:]
    write(out / 'package/u-boot.bin.lzo', packed)
    write(out / 'package/u-boot.uImage', header + packed)


def package_chain(out):
    spl = (out / 'early/spl.bin').read_bytes()
    tpl = (out / 'early/tpl-padded.bin').read_bytes()
    require(len(spl) + 24 <= 2048 and len(tpl) == 0x7800, 'Early layout overflow')
    rom = struct.pack('>III', 0x5509aaf6, len(spl) + 4, 0xbe220000) + spl
    rom += struct.pack('>III', 0x770888f7, 4, 0xbe220000)
    image = rom.ljust(2048, b'\xff') + tpl + (out / 'package/u-boot.uImage').read_bytes()
    require(len(image) <= 0x40000, 'Boot partition overflow')
    write(out / 'package/nandsource4-main-area.img', image)
    write(out / 'package/boot-main-256k.bin', image.ljust(0x40000, b'\xff'))


def package_oob(out):
    lib = ctypes.CDLL(str(out / 'native/ecc.so'))
    ptr = ctypes.POINTER(ctypes.c_ubyte)
    lib.nand_calculate_ecc.argtypes = [ctypes.c_void_p, ptr, ptr]
    lib.nand_calculate_ecc.restype = ctypes.c_int
    buf, ecc = (ctypes.c_ubyte * 256)(), (ctypes.c_ubyte * 3)()
    data = (out / 'package/boot-main-256k.bin').read_bytes()
    result = bytearray()
    for offset in range(0, len(data), 2048):
        page = data[offset:offset + 2048]
        oob = bytearray(b'\xff' * 64)
        for step in range(8):
            buf[:] = page[step * 256:(step + 1) * 256]
            require(lib.nand_calculate_ecc(None, buf, ecc) == 0, 'ECC calculation failed')
            oob[40 + step * 3:43 + step * 3] = bytes(ecc)
        result.extend(page + oob)
    require(len(result) == 128 * 2112, 'Raw page count differs')
    write(out / 'package/boot-main-oob.bin', result)


def audit(out):
    hashes = {}
    for name, wanted in LOCK['expected_outputs'].items():
        actual = digest((out / name).read_bytes())
        require(actual == wanted, 'Reference output differs: ' + name)
        hashes[name] = actual
    native = json.loads((out / 'native-tests.json').read_text())
    model = json.loads((out / 'mips-tests.json').read_text())
    require(native['passed'] and model['passed'] == 62, 'Tests incomplete')
    require('runtime error:' not in (out / 'native-tests.log').read_text(), 'Sanitizer error')
    symbols = {}
    for line in (out / 'early/tpl-symbols.txt').read_text().splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16) & 0xffffffff
    require(symbols['_start'] == 0xbe220800, 'TPL entry differs')
    require(symbols['__image_end'] <= 0xbe228000, 'TPL SRAM overflow')
    gap = 0xbe22f000 - symbols['__bss_end']
    require(gap >= 0x3000, 'Insufficient BSS/stack separation')
    config = (out / 'main/.config').read_text()
    for key in ['CONFIG_ENV_IS_NOWHERE=y', 'CONFIG_NAND_LANTIQ_RO=y',
                'CONFIG_SYS_BOOTM_LEN=0x1000000', 'CONFIG_BOOTCOMMAND="nandbootcheck"']:
        require(key in config, 'Missing baseline configuration: ' + key)
    for key in ['CONFIG_CMD_SAVEENV=y', 'CONFIG_CMD_ERASEENV=y', 'CONFIG_CMD_NAND=y']:
        require(key not in config, 'Unexpected write command: ' + key)
    for label, result in model['cases'].items():
        require(set(result['nand_commands']) <= {0, 0x30, 0x90}, 'Model wrote NAND: ' + label)
        require(not result['pages_read'] or max(result['pages_read']) < 128,
                'Model read outside boot partition: ' + label)
        if result['handoff']:
            require(result['decoded_sha256'] == hashes['package/u-boot.bin'],
                    'Model decoded a different main')
    report = {
        'passed': True, 'profile': LOCK['profile'], 'hardware_access': False,
        'built_main_from_source': True, 'built_spl_tpl_from_source': True,
        'legacy_binary_prefix_used': False, 'device_backup_required': False,
        'mips_chain_cases_passed': model['passed'], 'native_decoder_ecc_tests': native,
        'spl_bytes': (out / 'early/spl.bin').stat().st_size,
        'tpl_bytes': (out / 'early/tpl.bin').stat().st_size,
        'bss_stack_gap_bytes': gap, 'expected_output_sha256': hashes,
        'production_ready': False, 'default_linux_execution': False,
    }
    write(out / 'audit.json', (json.dumps(report, indent=2) + '\n').encode())
    print('PASS: source-only main/SPL/TPL/OOB; 7 reference hashes; 62 linked MIPS cases')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['main', 'chain', 'oob', 'audit'])
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    {'main': package_main, 'chain': package_chain, 'oob': package_oob,
     'audit': audit}[args.phase](args.output)


if __name__ == '__main__':
    main()
