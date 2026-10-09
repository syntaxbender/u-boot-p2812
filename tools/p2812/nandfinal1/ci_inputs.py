#!/usr/bin/env python3
"""Fetch the shared pinned builder/wheel and public OpenWrt test inputs."""
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parent = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('p2812_common_ci_inputs', parent / 'ci_inputs.py')
    shared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shared)
    shared.main()
    destination = Path(sys.argv[1])
    profile = json.loads((Path(__file__).parent / 'profile.json').read_text())
    base = 'https://downloads.openwrt.org/releases/25.12.0/targets/lantiq/xrx200/'
    for name, meta in profile['public_inputs'].items():
        shared.download(base + meta['filename'], destination / name, meta['sha256'])
    print('PASS: official OpenWrt initramfs/sysupgrade hashes verified')


if __name__ == '__main__':
    main()
