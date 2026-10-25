#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Create an OpenWrt files overlay from separately verified public inputs."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIRMWARE_SHA256 = '11b90138e69fd8b50df1b037b69d1f0e908750f344d895d8ac9b0862e3420d7a'
INIT_SHA256 = 'f704597b4993e7c38b0a8b389df88bcfcfd59b7fa12715b97bec961a76efbb0a'
FIRMWARE_PATH = 'lib/firmware/avm-vrx200-a-5.9.1.4.0.7.bin'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patch_init(data):
    if digest(data) != INIT_SHA256:
        raise ValueError('Unsupported dsl_control base; review this OpenWrt version first')
    text = data.decode()
    edits = [
        ('echo "[WaitForConfiguration]={\nlocs',
         'echo "[WaitForConfiguration]={\n${3:+dsmcs $3}\nlocs'),
        ('\tlocal macaddr\n', '\tlocal macaddr\n\tlocal vectoring\n'),
        ('\tconfig_get firmware dsl firmware\n',
         '\tconfig_get firmware dsl firmware\n'
         '\tconfig_get vectoring dsl vectoring\n'
         '\tcase "$vectoring" in\n'
         '\t\t0|1|2|"") ;;\n'
         '\t\t*) echo "invalid DSM vectoring mode"; return 1 ;;\n'
         '\tesac\n'),
        ('\tautoboot_script "$snr" "$macaddr"\n',
         '\tautoboot_script "$snr" "$macaddr" "$vectoring"\n'),
    ]
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError('Ambiguous init patch anchor')
        text = text.replace(old, new, 1)
    return text.encode()


def build(firmware, init, output):
    fw, base = firmware.read_bytes(), init.read_bytes()
    if len(fw) != 927124 or digest(fw) != FIRMWARE_SHA256:
        raise ValueError('AVM DSL firmware hash/size mismatch')
    patched = patch_init(base)
    firstboot = ('#!/bin/sh\n# P2812 AVM DSL companion: two DSL options only.\n'
                 'uci -q batch <<\'EOF\'\n'
                 f"set network.dsl.firmware='/{FIRMWARE_PATH}'\n"
                 "set network.dsl.vectoring='1'\n"
                 'commit network\nEOF\n').encode()
    files = {FIRMWARE_PATH: (fw, 0o644), 'etc/init.d/dsl_control': (patched, 0o755),
             'etc/uci-defaults/99-p2812-avm-dsl': (firstboot, 0o755)}
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, (data, mode) in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        hashes[name] = digest(data)
    manifest = {'firmware': 'AVM VR9 AnnexA, VDSL5.9.1.4.0.7',
                'OpenWrt_init_base_sha256': INIT_SHA256, 'files_sha256': hashes,
                'scope': 'OpenWrt files overlay, separate from U-Boot; no device access',
                'vectoring': 'Request full mode1 before training; active status depends on line/DSLAM'}
    (output.parent / (output.name + '-manifest.json')).write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--firmware', type=Path, required=True)
    p.add_argument('--init', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.firmware, a.init, a.output), indent=2))


if __name__ == '__main__':
    main()
