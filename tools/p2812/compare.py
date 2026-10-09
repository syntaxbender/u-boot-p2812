#!/usr/bin/env python3
"""Compare actual outputs of two independent completed baseline builds."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('first', type=Path)
    parser.add_argument('second', type=Path)
    args = parser.parse_args()
    lock = json.loads((Path(__file__).parent / 'baseline.json').read_text())
    reports = [json.loads((p / 'summary.json').read_text()) for p in [args.first, args.second]]
    if not all(r['passed'] for r in reports):
        raise RuntimeError('Both complete builds must pass')
    for key in ['source_commit', 'source_tree', 'source_archive_sha256', 'builder_image_id', 'unicorn_wheel_sha256']:
        if reports[0][key] != reports[1][key]:
            raise RuntimeError('Build inputs differ: ' + key)
    stable = set(lock['expected_outputs']) | {
        'main/u-boot', 'main/u-boot.bin', 'main/.config', 'main/System.map',
        'package/u-boot.bin.lzo', 'early/tpl-padded.bin', 'early/generated.h',
        'native-tests.json', 'mips-tests.json', 'audit.json', 'status.txt',
    }
    values = {}
    for name in sorted(stable):
        actual = [sha(p / 'results' / name) for p in [args.first, args.second]]
        if actual[0] != actual[1]:
            raise RuntimeError('Clean builds differ: ' + name)
        if name in lock['expected_outputs'] and actual[0] != lock['expected_outputs'][name]:
            raise RuntimeError('Historical reference differs: ' + name)
        values[name] = actual[0]
    report = {'passed': True, 'independent_builds': 2,
              'source_commit': reports[0]['source_commit'],
              'compared_outputs': len(values), 'historical_reference_matches': 7,
              'outputs_sha256': values, 'hardware_access': False}
    output = args.second / 'reproducibility.json'
    with output.open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print('PASS: %d outputs identical in two clean builds; all 7 historical binary references match' % len(values))


if __name__ == '__main__':
    main()
