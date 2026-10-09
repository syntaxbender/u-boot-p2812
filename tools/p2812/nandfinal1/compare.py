#!/usr/bin/env python3
"""Compare two completed Nandfinal1 source/test/build executions."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('first', type=Path)
    p.add_argument('second', type=Path)
    args = p.parse_args()
    summaries = [json.loads((r / 'summary.json').read_text()) for r in [args.first, args.second]]
    if not all(s['passed'] for s in summaries):
        raise RuntimeError('Both full builds must pass')
    for key in ['source_commit', 'source_tree', 'source_archive_sha256', 'builder_image_id', 'public_inputs_sha256', 'builder_archive_sha256', 'builder_recipe_sha256', 'builder_record_sha256', 'source_date_epoch', 'upstream_commit']:
        if summaries[0][key] != summaries[1][key]:
            raise RuntimeError('Build inputs differ: ' + key)
    stable = [
        'main/u-boot', 'main/u-boot.bin', 'main/.config', 'main/System.map',
        'main/linuxrun-layout.bin', 'main/startup-cache-tests.json', 'main/mdio-handoff-tests.json', 'main/upstream-version.txt',
        'package/u-boot.bin', 'package/u-boot.bin.lzo', 'package/u-boot.uImage',
        'package/nandfinal1-main-area.img', 'package/boot-main-256k.bin',
        'package/boot-main-oob.bin', 'package/manifest.json', 'early/spl.bin',
        'early/tpl.bin', 'early/tpl-padded.bin', 'early/generated.h',
        'fixtures/diagnostic.uImage', 'fixtures/diagnostic.dtb', 'fixtures/manifest.json',
        'linuxrun-mips-tests.json', 'autoboot-tests.json', 'mips-tests.json', 'native-tests.json', 'audit.json', 'status.txt']
    refs = {}
    for name in stable:
        values = [sha(r / 'results' / name) for r in [args.first, args.second]]
        if values[0] != values[1]:
            raise RuntimeError('Clean builds differ: ' + name)
        refs[name] = values[0]
    report = {'passed': True, 'source_commit': summaries[0]['source_commit'],
              'independent_builds': 2, 'identical_outputs': len(refs),
              'outputs_sha256': refs, 'hardware_access': False}
    with (args.second / 'reproducibility.json').open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print('PASS: %d Nandfinal1 firmware/test outputs identical in two clean builds' % len(refs))


if __name__ == '__main__':
    main()
