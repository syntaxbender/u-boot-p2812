#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fetch pinned vendor data, extract the DSL component and verify its overlay.

Run on Linux with make, gcc, patch, liblzma-dev and bspatch. No mount, vendor
program execution, network configuration or device access. Firmware stays local.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

import make_overlay

HERE = Path(__file__).resolve().parent


def fetch(item, cache):
    path = cache / item['name']
    if not path.exists():
        req = urllib.request.Request(item['url'], headers={'User-Agent': 'P2812-DSL-verifier/1'})
        with urllib.request.urlopen(req, timeout=90) as response:
            data = response.read(64 * 1024 * 1024)
        if make_overlay.digest(data) != item['sha256']:
            raise ValueError('Download hash mismatch: ' + item['name'])
        path.write_bytes(data)
    if make_overlay.digest(path.read_bytes()) != item['sha256']:
        raise ValueError('Cached input hash mismatch: ' + item['name'])
    return path


def unpack_sources(archive, destination):
    with tarfile.open(archive) as stream:
        for member in stream:
            target = destination / member.name
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Archive path escapes source directory')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stream.extractfile(member).read())
            else:
                raise ValueError('Unexpected non-file source archive entry')


def verify(work, cache, fetch_only=False):
    lock = json.loads((HERE / 'inputs.lock.json').read_text())
    cache.mkdir(parents=True, exist_ok=True)
    items = [lock['vendor_image'], lock['squashfs'], lock['openwrt_init'], *lock['patches']]
    inputs = {item['name']: fetch(item, cache) for item in items}
    if fetch_only:
        return {'input_count': len(items), 'all_hashes_verified': True}
    work.mkdir(parents=True, exist_ok=False)
    unpack_sources(inputs[lock['squashfs']['name']], work)
    source = work / 'squashfs4.3'

    def run(argv, cwd=None):
        subprocess.run([str(x) for x in argv], cwd=cwd, check=True)

    for item in lock['patches']:
        run(['patch', '-p0', '--batch', '-i', inputs[item['name']].resolve()], cwd=source)
    run(['make', '-C', source / 'squashfs-tools', '-j2', 'unsquashfs',
         'EXTRA_CFLAGS=-O2 -fcommon -DTARGET_FORMAT=AVM_BE -std=gnu17',
         'LEGACY_FORMATS_SUPPORT=0', 'GZIP_SUPPORT=0', 'LZMA_SUPPORT=0',
         'LZMA_XZ_SUPPORT=0', 'XZ_SUPPORT=1', 'XATTR_SUPPORT=0',
         'XATTR_DEFAULT=0', 'LINK_COMPRESSION_LIBS_STATICALLY=0', 'COMP_DEFAULT=xz'])
    tool = (source / 'squashfs-tools/unsquashfs').resolve()
    image = work / 'filesystem.image'
    with tarfile.open(inputs[lock['vendor_image']['name']]) as archive:
        member = archive.getmember('./var/tmp/filesystem.image')
        if not member.isfile() or member.size != 35012616:
            raise ValueError('Unexpected AVM filesystem member')
        image.write_bytes(archive.extractfile(member).read())
    if make_overlay.digest(image.read_bytes()) != lock['filesystem_sha256']:
        raise ValueError('AVM filesystem checksum mismatch')
    outer, core = work / 'outer', work / 'core'
    run([tool, '-no-progress', '-processors', '1', '-d', outer, image,
         'filesystem_core.squashfs'])
    run([tool, '-no-progress', '-processors', '1', '-d', core,
         outer / 'filesystem_core.squashfs', 'lib/modules/dsp_vr9/vr9-B-dsl.bin',
         'lib/modules/dsp_vr9/vr9-A-dsl.bin.bsdiff', 'lib/modules/dsp_vr9/vr9-A-dsl.bin.md5sum'])
    dsp = core / 'lib/modules/dsp_vr9'
    firmware = work / 'vr9-A-dsl.bin'
    run(['bspatch', dsp / 'vr9-B-dsl.bin', firmware, dsp / 'vr9-A-dsl.bin.bsdiff'])
    data = firmware.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    if md5 != lock['firmware']['md5'] or md5 not in (dsp / 'vr9-A-dsl.bin.md5sum').read_text():
        raise ValueError('AVM embedded checksum mismatch')
    manifest = make_overlay.build(firmware, inputs[lock['openwrt_init']['name']], work / 'files')
    if manifest['files_sha256']['etc/init.d/dsl_control'] != lock['expected_init_sha256']:
        raise ValueError('Init output differs from device-verified recipe')
    # This is the only CI-uploaded result. Firmware and extracted vendor files
    # remain in the runner's temporary work directory.
    result = {'verified': True, 'vendor_image_url': lock['vendor_image']['url'],
              'vendor_image_sha256': lock['vendor_image']['sha256'],
              'firmware_sha256': make_overlay.digest(data), 'firmware_bytes': len(data),
              'embedded_MD5_match': True, 'overlay': manifest,
              'qualification': lock['qualification'],
              'vendor_signature_cryptographically_verified': False,
              'hardware_access': False, 'firmware_published': False}
    (work / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--fetch-only', action='store_true')
    a = p.parse_args()
    print(json.dumps(verify(a.work.resolve(), a.cache.resolve(), a.fetch_only), indent=2))


if __name__ == '__main__':
    main()
