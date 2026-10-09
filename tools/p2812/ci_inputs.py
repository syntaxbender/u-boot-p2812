#!/usr/bin/env python3
"""Fetch hash-pinned public CI inputs; invoked before the offline builds."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

KIT = Path(__file__).resolve().parent


def download(url, path, expected):
    if urllib.parse.urlparse(url).scheme != 'https':
        raise ValueError('HTTPS download URL required')
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, path.open('xb') as output:
        if urllib.parse.urlparse(response.url).scheme != 'https':
            raise ValueError('HTTPS redirect required')
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            output.write(block)
            digest.update(block)
    if digest.hexdigest() != expected:
        raise RuntimeError('Downloaded input SHA256 differs: ' + path.name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--no-builder', action='store_true', help='Build the environment from the CI recipe instead')
    args = parser.parse_args()
    lock = json.loads((KIT / 'baseline.json').read_text())
    builder_url = os.environ.get('P2812_BUILDER_ARCHIVE_URL', '')
    if not args.no_builder and not builder_url:
        raise RuntimeError('Publish the frozen builder archive and set repository variable P2812_BUILDER_ARCHIVE_URL first')
    args.destination.mkdir(parents=True, exist_ok=False)
    if not args.no_builder:
        download(builder_url, args.destination / 'builder.tar', lock['builder_archive_sha256'])
    with urllib.request.urlopen('https://pypi.org/pypi/unicorn/2.1.4/json', timeout=60) as stream:
        metadata = json.load(stream)
    expected = lock['unicorn_wheel']
    records = [r for r in metadata['urls'] if r['filename'] == expected['filename']]
    if len(records) != 1 or records[0]['digests']['sha256'] != expected['sha256']:
        raise RuntimeError('Public wheel metadata differs from the pinned input')
    download(records[0]['url'], args.destination / 'unicorn.whl', expected['sha256'])
    print('PASS: public Linux wheel SHA256 verified; builder=' + ('source-built separately' if args.no_builder else 'archive verified'))


if __name__ == '__main__':
    main()
