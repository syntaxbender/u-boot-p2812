#!/usr/bin/env python3
"""Prepare public kernel fixtures and the RAM/NAND-disabled diagnostic image."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path
import platform
import struct
import subprocess
import tarfile
import zlib


def sha(data):
    return hashlib.sha256(data).hexdigest()


def image(payload, name):
    packed = lzma.compress(payload, format=lzma.FORMAT_ALONE, preset=6)
    packed = packed[:5] + struct.pack('<Q', len(payload)) + packed[13:]
    header = struct.pack('>7I4B32s', 0x27051956, 0, 1791504000, len(packed),
                         0x80002000, 0x80002000, zlib.crc32(packed),
                         5, 5, 2, 3, name.encode())
    header = header[:4] + struct.pack('>I', zlib.crc32(header)) + header[8:]
    return header + packed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--sysupgrade', type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--diagnostic', type=Path)
    source.add_argument('--initramfs', type=Path)
    args = parser.parse_args()
    if platform.system() != 'Linux':
        raise RuntimeError('Use the pinned Linux builder')
    args.output.mkdir(exist_ok=False)
    with tarfile.open(args.sysupgrade) as archive:
        official = archive.extractfile('sysupgrade-P2812HNUF1/kernel').read()
    if sha(official) != '96393a1374fbe10f0922d7588b71d43319808739d5e216ed2ae858fa7497f63a':
        raise RuntimeError('Official kernel reference differs')
    if args.diagnostic:
        original = args.diagnostic.read_bytes()
        if sha(original) != '0179289bc2c55b47ba892ec0e6abc3b7023259116b29b3a040cdb038628a01e9':
            raise RuntimeError('Previously verified diagnostic reference differs')
        payload = lzma.decompress(original[64:], format=lzma.FORMAT_ALONE)
    else:
        original = args.initramfs.read_bytes()
        if sha(original) != 'b0ec7302744076eef38ee0df732fb715ae108ab36327f334a9f5537c788be382':
            raise RuntimeError('Official initramfs reference differs')
        raw = lzma.decompress(original[64:], format=lzma.FORMAT_ALONE)
        if sha(raw) != '09f5b4a312cc6943c9c64f1ea019a9787d58fc157b498f05b5781253da0fc72c':
            raise RuntimeError('Official decoded initramfs reference differs')
        patched = args.output / 'board-patched.dtb'
        patched.write_bytes(raw[0xf06720:])
        subprocess.run(['fdtput', '-t', 's', str(patched), '/chosen', 'bootargs',
                        'console=ttyLTQ0,115200 rdinit=/bin/ash panic=0'], check=True)
        subprocess.run(['fdtput', '-t', 's', str(patched),
                        '/fpi@10000000/localbus@0/flash@0', 'status', 'disabled'], check=True)
        payload = raw[:0xf06720] + patched.read_bytes()
    if sha(payload) != '1b7fa8043a04168eac20861714561342f38ce28084ad841df02c7c9808142900':
        raise RuntimeError('Diagnostic kernel/DTB payload differs')
    dtb = args.output / 'diagnostic.dtb'
    dtb.write_bytes(payload[0xf06720:])
    def prop(node, key):
        return subprocess.check_output(['fdtget', '-t', 's', str(dtb), node, key], text=True).strip()
    if prop('/fpi@10000000/localbus@0/flash@0', 'status') != 'disabled':
        raise RuntimeError('Diagnostic NAND is enabled')
    if prop('/chosen', 'bootargs') != 'console=ttyLTQ0,115200 rdinit=/bin/ash panic=0':
        raise RuntimeError('Diagnostic init/command line differs')
    images = {'tiny': image(bytes(range(256)) * 16, 'P2812 model entry only'),
              'official': official,
              'diagnostic': image(payload, 'OpenWrt25.12 NANDFINAL1 RAM test')}
    report = {'hardware_access': False, 'diagnostic_reference_sha256': sha(original),
              'diagnostic_reference_type': 'official-initramfs' if args.initramfs else 'prior-diagnostic',
              'diagnostic_kernel_and_dtb_unchanged': True,
              'diagnostic_compression_dictionary': 0x800000, 'images': {}}
    for name, data in images.items():
        raw = lzma.decompress(data[64:], format=lzma.FORMAT_ALONE)
        header = bytearray(data[:64]); header[4:8] = bytes(4)
        if (zlib.crc32(header) != struct.unpack_from('>I', data, 4)[0] or
                zlib.crc32(data[64:]) != struct.unpack_from('>I', data, 24)[0]):
            raise RuntimeError('Fixture CRC differs')
        (args.output / (name + '.uImage')).write_bytes(data)
        (args.output / (name + '.raw')).write_bytes(raw)
        report['images'][name] = {'bytes': len(data), 'sha256': sha(data),
                                 'decoded_bytes': len(raw), 'decoded_sha256': sha(raw)}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: public kernel and NAND-disabled diagnostic fixtures; 8MiB LZMA dictionary')


if __name__ == '__main__':
    main()
