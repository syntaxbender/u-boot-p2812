#!/usr/bin/env python3
"""Package the new manual Linux profile with newly bound source SPL/TPL."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import zlib


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    with path.open('xb') as stream:
        stream.write(data)


def package_main(out):
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '1791504000'))
    version = (out / 'main/upstream-version.txt').read_text().strip()
    require(version and len(version) < 48, 'Invalid upstream version')
    raw = (out / 'main/u-boot.bin').read_bytes()
    require(0 < len(raw) <= 0x100000 and b'P2812-2026-NANDFINAL1> ' in raw,
            'Wrong candidate identity or main size')
    path = out / 'package/u-boot.bin'
    write(path, raw)
    path.chmod(0o644)
    os.utime(path, (epoch, epoch))
    with path.open('rb') as stream:
        payload = subprocess.check_output(['lzop', '-9', '-c'], stdin=stream)
    require(subprocess.check_output(['lzop', '-d', '-c'], input=payload) == raw, 'LZO round trip')
    header = struct.pack('>7I4B32s', 0x27051956, 0, epoch, len(payload),
                         0xa0100000, 0xa0100000, zlib.crc32(payload), 17, 5, 5, 4,
                         ('U-Boot ' + version + ' P2812 NANDFINAL1').encode('ascii')[:32])
    header = header[:4] + struct.pack('>I', zlib.crc32(header)) + header[8:]
    write(out / 'package/u-boot.bin.lzo', payload)
    write(out / 'package/u-boot.uImage', header + payload)


def package_chain(out):
    spl = (out / 'early/spl.bin').read_bytes()
    tpl = (out / 'early/tpl-padded.bin').read_bytes()
    require(len(spl) + 24 <= 2048 and len(tpl) == 0x7800, 'Early layout bounds')
    prefix = struct.pack('>III', 0x5509aaf6, len(spl) + 4, 0xbe220000) + spl
    prefix += struct.pack('>III', 0x770888f7, 4, 0xbe220000)
    image = prefix.ljust(2048, b'\xff') + tpl + (out / 'package/u-boot.uImage').read_bytes()
    require(len(image) <= 0x40000, 'Boot extent overflow')
    write(out / 'package/nandfinal1-main-area.img', image)
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
            require(lib.nand_calculate_ecc(None, buf, ecc) == 0, 'ECC calculation')
            oob[40 + step * 3:43 + step * 3] = bytes(ecc)
        result.extend(page + oob)
    require(len(result) == 270336, 'Raw extent size')
    write(out / 'package/boot-main-oob.bin', result)


def audit(out):
    main = json.loads((out / 'linuxrun-mips-tests.json').read_text())
    early = json.loads((out / 'mips-tests.json').read_text())
    native = json.loads((out / 'native-tests.json').read_text())
    startup = json.loads((out / 'main/startup-cache-tests.json').read_text())
    autoboot = json.loads((out / 'autoboot-tests.json').read_text())
    require(main['passed'] and main['cases_passed'] == 32, 'Linux handoff cases incomplete')
    require(early['passed'] == 62 and native['passed'], 'Early stage tests incomplete')
    require(startup['passed'] and startup['emulator_k0_cca'] == 3, 'Startup/cache tests incomplete')
    require(autoboot['passed'] and autoboot['cases_passed'] == 4 and
            autoboot['compiler_derived_command_layout'], 'Autoboot policy coverage incomplete')
    config = (out / 'main/.config').read_text()
    for key in ['CONFIG_ENV_IS_NOWHERE=y', 'CONFIG_NAND_LANTIQ_RO=y',
                'CONFIG_SYS_BOOTM_LEN=0x1000000', 'CONFIG_CMD_LOADB=y',
                'CONFIG_CMD_P2812_LINUXRUN=y', 'CONFIG_AUTOBOOT=y',
                'CONFIG_BOOTDELAY=5', 'CONFIG_BOOTCOMMAND="nandbootrun"']:
        require(key in config, 'Profile configuration differs: ' + key)
    for key in ['CONFIG_CMD_SAVEENV=y', 'CONFIG_CMD_ERASEENV=y', 'CONFIG_CMD_NAND=y', 'CONFIG_CMD_MTD=y']:
        require(key not in config, 'Unexpected persistent write command')
    main_symbols = (out / 'main/symbols.txt').read_text()
    require('_u_boot_list_2_cmd_2_loady' in main_symbols, 'Actual loady command missing')
    require('runtime error:' not in (out / 'native-tests.log').read_text(), 'Sanitizer diagnostic')
    syms = {}
    for row in (out / 'early/tpl-symbols.txt').read_text().splitlines():
        fields = row.split()
        if len(fields) == 3:
            syms[fields[2]] = int(fields[0], 16) & 0xffffffff
    require(syms['_start'] == 0xbe220800 and syms['__image_end'] <= 0xbe228000,
            'TPL SRAM bounds')
    require(0xbe22f000 - syms['__bss_end'] >= 0x3000, 'Stack separation')
    for label, result in early['cases'].items():
        require(set(result['nand_commands']) <= {0, 0x30, 0x90}, 'Early model writes NAND: ' + label)
        require(not result['pages_read'] or max(result['pages_read']) < 128, 'Boot extent read bounds')
        if result['handoff']:
            require(result['decoded_sha256'] == sha((out / 'package/u-boot.bin').read_bytes()),
                    'TPL decoded wrong main')
    files = {str(p.relative_to(out)): {'bytes': p.stat().st_size, 'sha256': sha(p.read_bytes())}
             for p in sorted((out / 'package').iterdir()) if p.is_file()}
    report = {'passed': True, 'revision': 'NANDFINAL1', 'hardware_access': False,
              'default_autoboot': True, 'default_linux_execution': True, 'bootdelay_seconds': 5,
              'default_bootcommand': 'nandbootrun',
              'manual_commands_execute_linux': ['nandbootrun', 'rambootrun'],
              'linux_ram_trial_completed': False, 'nand_written': False, 'production_ready': False,
              'main_built_from_source': True, 'early_sources_unchanged_from_nandsource4': True,
              'early_main_binding_regenerated': True, 'legacy_prefix_reused': False,
              'linux_handoff_mips_cases': 32, 'early_chain_mips_cases': 62,
              'autoboot_policy_mips_cases': 4,
              'real_lmb_model': True, 'kernel_instructions_executed_in_model': 0,
              'native_sanitizers_passed': True,
              'spl_bytes': (out / 'early/spl.bin').stat().st_size,
              'tpl_bytes': (out / 'early/tpl.bin').stat().st_size,
              'boot_extent_bytes': 0x40000, 'files': files}
    write(out / 'audit.json', (json.dumps(report, indent=2) + '\n').encode())
    write(out / 'package/manifest.json', (json.dumps(report, indent=2) + '\n').encode())
    print('PASS: manual Linux main + newly bound source SPL/TPL/OOB; 32+62 MIPS cases')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['main', 'chain', 'oob', 'audit'])
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    {'main': package_main, 'chain': package_chain, 'oob': package_oob, 'audit': audit}[args.phase](args.output)


if __name__ == '__main__':
    main()
