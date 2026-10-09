#!/usr/bin/env python3
"""Build/test the manual Linux candidate from a committed upstream-based tree."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
KIT = ROOT / 'tools/p2812'


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            result.update(block)
    return result.hexdigest()


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--sysupgrade', type=Path, required=True)
    parser.add_argument('--initramfs', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--image', default=None, help='Loaded frozen builder tag or ID')
    parser.add_argument('--builder-record', type=Path, help='CI source-built environment verification record')
    args = parser.parse_args()
    profile = json.loads((KIT / 'nandfinal1/profile.json').read_text())
    if git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Clean committed candidate required')
    subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor',
                    profile['upstream_commit'], 'HEAD'], check=True)
    provenance = json.loads((KIT / 'provenance.json').read_text())
    for name, expected in provenance['source_files_sha256'].items():
        if sha(KIT / 'early' / name) != expected:
            raise RuntimeError('Previously proven early source changed: ' + name)
    supplied = {'unicorn.whl': args.wheel.resolve(), 'sysupgrade.bin': args.sysupgrade.resolve(),
                'initramfs.bin': args.initramfs.resolve()}
    expected = {'unicorn.whl': profile['unicorn_wheel']['sha256']}
    expected.update({name: meta['sha256'] for name, meta in profile['public_inputs'].items()})
    for name, path in supplied.items():
        if sha(path) != expected[name]:
            raise RuntimeError('Public build/test input differs: ' + name)
    out = args.output.resolve()
    if out == ROOT or ROOT in out.parents or out.exists():
        raise RuntimeError('Fresh output directory outside checkout required')
    print('PASS: committed candidate, proven early source and public test inputs; no device access', flush=True)
    if args.check_only:
        return
    builder_record = json.loads(args.builder_record.read_text()) if args.builder_record else None
    if profile.get('builder_recipe_sha256'):
        if not builder_record or not builder_record.get('passed') or \
           builder_record['recipe_sha256'] != profile['builder_recipe_sha256'] or \
           builder_record['packages_sha256'] != profile['builder_packages_sha256']:
            raise RuntimeError('Source-built environment recipe/package verification required')
    elif builder_record:
        raise RuntimeError('This source profile does not authorize a source-built environment')
    requested = args.image or (builder_record['image_id'] if builder_record else profile['builder_image_id'])
    image = subprocess.check_output(['docker', 'image', 'inspect', requested,
                                     '--format', '{{.Id}}'], text=True).strip()
    allowed = ([builder_record['image_id']] if builder_record else
               [profile['builder_image_id']] + profile.get('builder_alternate_image_ids', []))
    if image not in allowed:
        raise RuntimeError('Loaded image does not match the hash-pinned builder archive')
    if builder_record:
        packages = subprocess.check_output(['docker', 'run', '--rm', '--platform', 'linux/amd64',
                    '--network', 'none', '--entrypoint', 'dpkg-query', image, '-W'])
        if hashlib.sha256(packages).hexdigest() != profile['builder_packages_sha256']:
            raise RuntimeError('Actual source-built container package inventory differs')
    (out / 'inputs').mkdir(parents=True)
    (out / 'results').mkdir()
    archive = out / 'inputs/source.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git', '-C', str(ROOT), 'archive', 'HEAD'], stdout=stream, check=True)
    info = {'source_commit': git('rev-parse', 'HEAD'), 'source_tree': git('rev-parse', 'HEAD^{tree}'),
            'source_archive_sha256': sha(archive), 'builder_image_id': image,
            'builder_archive_sha256': profile.get('builder_archive_sha256'),
            'builder_recipe_sha256': profile.get('builder_recipe_sha256'),
            'builder_record_sha256': sha(args.builder_record) if args.builder_record else None,
            'source_date_epoch': profile['source_date_epoch'],
            'upstream_commit': profile['upstream_commit'],
            'public_inputs_sha256': expected, 'start_utc': datetime.now(timezone.utc).isoformat(),
            'hardware_access': False}
    (out / 'inputs/identity.json').write_text(json.dumps(info, indent=2) + '\n')
    cmd = ['docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
           '--env', 'PYTHONDONTWRITEBYTECODE=1',
           '--env', 'SOURCE_DATE_EPOCH=' + str(profile['source_date_epoch']),
           '--mount', 'type=bind,src=%s,dst=/inputs/source.tar,readonly' % archive,
           '--mount', 'type=bind,src=%s,dst=/results' % (out / 'results')]
    for name, path in supplied.items():
        cmd += ['--mount', 'type=bind,src=%s,dst=/inputs/%s,readonly' % (path, name)]
    cmd += ['--entrypoint', '/bin/bash', image, '-euc',
            'mkdir /source; tar -C /source -xf /inputs/source.tar; bash /source/tools/p2812/nandfinal1/build.sh']
    (out / 'inputs/command.json').write_text(json.dumps(cmd, indent=2) + '\n')
    with (out / 'console.log').open('wb') as stream:
        result = subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT)
    if sha(archive) != info['source_archive_sha256'] or any(sha(path) != expected[name] for name, path in supplied.items()):
        raise RuntimeError('Execution input changed')
    info.update(passed=result.returncode == 0, exit_code=result.returncode,
                end_utc=datetime.now(timezone.utc).isoformat(),
                outputs_sha256={str(p.relative_to(out / 'results')): sha(p)
                                for p in sorted((out / 'results').rglob('*')) if p.is_file()})
    (out / 'summary.json').write_text(json.dumps(info, indent=2) + '\n')
    if result.returncode:
        print((out / 'console.log').read_text()[-5000:])
        raise RuntimeError('Candidate build/tests failed; original logs preserved')
    print('PASS: NANDFINAL1 main/early stages/fixtures/models complete: ' + str(out), flush=True)


if __name__ == '__main__':
    main()
