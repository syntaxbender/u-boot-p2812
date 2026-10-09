#!/usr/bin/env python3
"""Build a committed source-only baseline in the pinned offline container."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / 'tools/p2812'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path, help='fresh directory outside the source checkout')
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    lock = json.loads((KIT / 'baseline.json').read_text())
    provenance = json.loads((KIT / 'provenance.json').read_text())
    if git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Commit source changes before building; checkout must be clean')
    subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor',
                    lock['upstream_commit'], 'HEAD'], check=True)
    for name, expected in lock['main_files_sha256'].items():
        if digest(ROOT / name) != expected:
            raise RuntimeError('Validated main source changed: ' + name)
    for name, expected in provenance['source_files_sha256'].items():
        if digest(KIT / 'early' / name) != expected:
            raise RuntimeError('Validated early source changed: ' + name)
    changed = set(git('diff', '--name-only', lock['upstream_commit'], 'HEAD').splitlines())
    unexpected = {name for name in changed if name not in lock['main_files_sha256']
                  and not name.startswith('tools/p2812/')
                  and name != '.github/workflows/p2812.yml'}
    if unexpected:
        raise RuntimeError('Unreviewed upstream changes: ' + repr(sorted(unexpected)))
    wheel = args.wheel.resolve()
    if digest(wheel) != lock['unicorn_wheel']['sha256']:
        raise RuntimeError('Pinned Linux/amd64 wheel differs')
    out = args.output.resolve()
    if out == ROOT or ROOT in out.parents:
        raise RuntimeError('Output must be outside the source checkout')
    if out.exists():
        raise RuntimeError('Output exists; select a fresh run directory')
    print('PASS: committed upstream/main/early source and wheel verified; no device I/O', flush=True)
    if args.check_only:
        return
    image = lock['builder_image_id']
    actual = subprocess.check_output(['docker', 'image', 'inspect', image,
                                      '--format', '{{.Id}}'], text=True).strip()
    if actual != image:
        raise RuntimeError('Pinned builder image differs')
    (out / 'inputs').mkdir(parents=True)
    (out / 'results').mkdir()
    archive = out / 'inputs/source.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', 'HEAD'],
                       stdout=stream, check=True)
    identity = {
        'source_commit': git('rev-parse', 'HEAD'),
        'source_tree': git('rev-parse', 'HEAD^{tree}'),
        'source_archive_sha256': digest(archive),
        'builder_image_id': image, 'platform': 'linux/amd64', 'network': 'none',
        'unicorn_wheel_sha256': digest(wheel), 'hardware_access': False,
        'start_utc': datetime.now(timezone.utc).isoformat(),
    }
    (out / 'inputs/identity.json').write_text(json.dumps(identity, indent=2) + '\n')
    command = [
        'docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
        '--env', 'PYTHONDONTWRITEBYTECODE=1',
        '--mount', 'type=bind,src=%s,dst=/inputs/source.tar,readonly' % archive,
        '--mount', 'type=bind,src=%s,dst=/inputs/unicorn.whl,readonly' % wheel,
        '--mount', 'type=bind,src=%s,dst=/results' % (out / 'results'),
        '--entrypoint', '/bin/bash', image, '-euc',
        'mkdir /source; tar -C /source -xf /inputs/source.tar; bash /source/tools/p2812/build.sh',
    ]
    (out / 'inputs/docker-command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'console.log').open('wb') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
    if digest(archive) != identity['source_archive_sha256'] or digest(wheel) != identity['unicorn_wheel_sha256']:
        raise RuntimeError('Execution input changed')
    outputs = {str(p.relative_to(out / 'results')): digest(p)
               for p in sorted((out / 'results').rglob('*')) if p.is_file()}
    report = dict(identity, end_utc=datetime.now(timezone.utc).isoformat(),
                  exit_code=result.returncode, passed=result.returncode == 0,
                  outputs_sha256=outputs)
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    if result.returncode:
        print((out / 'console.log').read_text()[-4000:])
        raise RuntimeError('Build failed; complete logs preserved at ' + str(out))
    for name, expected in lock['expected_outputs'].items():
        if outputs.get(name) != expected:
            raise RuntimeError('Expected output differs: ' + name)
    print('PASS: complete source-only build/tests and 7 reference hashes: ' + str(out), flush=True)


if __name__ == '__main__':
    main()
