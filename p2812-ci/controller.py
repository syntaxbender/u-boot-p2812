#!/usr/bin/env python3
"""Stable upstream -> pinned P2812 patch -> two builds -> versioned release.

Remote writes are available only inside this repository's GitHub Actions job.
The local validation path prepares sources and artifacts without publishing.
"""
import argparse
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

KIT = Path(__file__).resolve().parent
API = 'https://api.github.com'
TAG = re.compile(r'v(20\d{2})\.(\d{2})(?:\.(\d+))?\Z')


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def command(args, cwd=None, env=None, capture=True):
    p = subprocess.run(args, cwd=cwd, env=env, check=True,
                       stdout=subprocess.PIPE if capture else None, text=True)
    return p.stdout.strip() if capture else ''


def git(source, *args, env=None):
    return command(['git', '-C', str(source), *args], env=env)


def configuration():
    c = json.loads((KIT / 'config.json').read_text())
    need(c['upstream_repository'] == 'https://github.com/u-boot/u-boot.git', 'Unexpected upstream')
    need(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', c['repository']) and
         c['repository'] != 'u-boot/u-boot', 'An owned fork is required')
    need(sha(KIT / 'port.patch') == c['port_patch_sha256'], 'Port patch SHA differs')
    need(environment_id() == c['builder_recipe_sha256'], 'Build environment recipe differs')
    need(sha(KIT / 'builder/packages.lock') == c['builder_packages_sha256'], 'Package inventory differs')
    return c


def environment_id():
    h = hashlib.sha256()
    for name in ('Dockerfile', 'packages.lock'):
        h.update(name.encode() + b'\0' + (KIT / 'builder' / name).read_bytes() + b'\0')
    return h.hexdigest()


def recipe_id():
    h = hashlib.sha256()
    for name in ('config.json', 'port.patch', 'controller.py', 'builder/Dockerfile', 'builder/packages.lock'):
        h.update(name.encode() + b'\0' + (KIT / name).read_bytes() + b'\0')
    return h.hexdigest()


def latest_stable(text):
    refs = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or not re.fullmatch(r'[0-9a-f]{40}', fields[0]):
            continue
        oid, ref = fields
        if not ref.startswith('refs/tags/'):
            continue
        name = ref[len('refs/tags/'):]
        peeled = name.endswith('^{}')
        name = name.removesuffix('^{}')
        m = TAG.fullmatch(name)
        if not m:
            continue
        if name not in refs or peeled:
            refs[name] = (oid, tuple(int(x or 0) for x in m.groups()))
    need(refs, 'No stable U-Boot tag found')
    tag = max(refs, key=lambda n: refs[n][1])
    return tag, refs[tag][0]


def github(method, path, payload=None, authenticated=False, raw=None):
    url = path if path.startswith('https://') else API + path
    host = urllib.parse.urlparse(url).hostname
    need(host in ('api.github.com', 'uploads.github.com'), 'Unexpected GitHub API host')
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'p2812-upstream-ci',
               'X-GitHub-Api-Version': '2022-11-28'}
    if authenticated:
        need(os.environ.get('GH_TOKEN'), 'GitHub Actions token is missing')
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    body = raw
    if payload is not None:
        body = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    elif raw is not None:
        headers['Content-Type'] = 'application/octet-stream'
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=300 if raw is not None else 60) as response:
            data = response.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        if error.code == 404 and method == 'GET':
            return None
        raise RuntimeError(f'GitHub {method} {path}: HTTP {error.code}') from error


def write_guard(c):
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and
         os.environ.get('GITHUB_REPOSITORY') == c['repository'],
         'Remote writes require this fork\'s GitHub Actions job')
    need(os.environ.get('GITHUB_REF') == 'refs/heads/' + c['default_branch'],
         'Publication is restricted to the automation default branch')


def discover(c, work):
    work.mkdir(parents=True, exist_ok=False)
    tags = command(['git', 'ls-remote', '--tags', c['upstream_repository'], 'v20*'])
    tag, oid = latest_stable(tags)
    recipe = recipe_id()
    release = f'p2812-{tag}-{oid[:12]}-{recipe[:10]}'
    existing = github('GET', f'/repos/{c["repository"]}/releases/tags/{release}')
    selection = dict(upstream_tag=tag, upstream_commit=oid, recipe_sha256=recipe,
                     release_tag=release, build_required=not existing or existing['draft'])
    write_json(work / 'selection.json', selection)
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a') as f:
            f.write('build=' + str(selection['build_required']).lower() + '\n')
            f.write('release_tag=' + release + '\n')
    print(json.dumps(selection, indent=2))


def heartbeat(c):
    write_guard(c)
    path = f'/repos/{c["repository"]}/contents/.p2812-ci-heartbeat'
    existing = github('GET', path + '?ref=' + c['default_branch'], authenticated=True)
    now = datetime.now(timezone.utc)
    if existing:
        content = json.loads(base64.b64decode(existing['content']))
        then = datetime.fromisoformat(content['last_heartbeat_utc'])
        if (now - then).days < 25:
            print('Heartbeat is current; no repository write')
            return
    content = json.dumps({'last_heartbeat_utc': now.isoformat(),
                          'purpose': 'Keep the quarterly stable-release monitor active'}, indent=2) + '\n'
    payload = dict(message='ci: keep stable upstream monitoring active',
                   content=base64.b64encode(content.encode()).decode(), branch=c['default_branch'])
    if existing:
        payload['sha'] = existing['sha']
    github('PUT', path, payload, authenticated=True)
    print('Monthly monitor heartbeat updated')


def build_environment(c, work):
    """Build tools from public pinned sources; never upload an environment."""
    need(not (work / 'builder-record.json').exists(), 'Environment already verified')
    context = KIT / 'builder'
    need({p.name for p in context.iterdir()} == {'Dockerfile', 'packages.lock'}, 'Unexpected build context file')
    image = 'p2812-ci-builder:' + c['builder_recipe_sha256'][:16]
    args = ['docker', 'build', '--platform', 'linux/amd64', '--progress', 'plain',
            '--label', 'org.p2812.builder-recipe=' + c['builder_recipe_sha256'],
            '--tag', image, str(context)]
    with (work / 'builder-build.log').open('xb') as log:
        result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        print((work / 'builder-build.log').read_text()[-6000:])
        raise RuntimeError('Pinned source-built environment failed')
    identity = command(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'])
    packages = subprocess.check_output(['docker', 'run', '--rm', '--platform', 'linux/amd64',
                '--network', 'none', '--entrypoint', 'dpkg-query', identity, '-W'])
    digest = hashlib.sha256(packages).hexdigest()
    need(digest == c['builder_packages_sha256'], 'Actual container packages differ from lock')
    record = dict(schema=1, passed=True, recipe_sha256=c['builder_recipe_sha256'],
                  packages_sha256=digest, package_count=len(packages.splitlines()), image_id=identity,
                  base_image=c['builder_base_image'], snapshot=c['builder_snapshot'],
                  source_built=True, private_environment_archive_used=False)
    write_json(work / 'builder-record.json', record)
    print('Built and verified public-source environment: ' + identity)


def prepare(c, work, local_source=None):
    selection = json.loads((work / 'selection.json').read_text())
    need(selection['recipe_sha256'] == recipe_id(), 'Build recipe changed after discovery')
    need(TAG.fullmatch(selection['upstream_tag']), 'Only stable tags are supported')
    source = work / 'source'
    need(not source.exists(), 'Source directory already exists; do not reuse a dirty build')
    command(['git', 'init', '-q', str(source)])
    remote = str(local_source.resolve()) if local_source else c['upstream_repository']
    fetch = ['fetch', '--no-tags']
    if local_source:
        fetch.append('--update-shallow')
    git(source, *fetch, remote, selection['upstream_commit'])
    git(source, 'checkout', '-q', '--detach', 'FETCH_HEAD')
    need(git(source, 'rev-parse', 'HEAD') == selection['upstream_commit'], 'Wrong upstream revision')
    # Stage the exact patch, including newly added files matched by upstream
    # ignore rules (*.patch and, on case-insensitive hosts, *.s versus *.S).
    # A later plain `git add -A` would silently omit those from git archive.
    git(source, 'apply', '--check', '--index', str(KIT / 'port.patch'))
    git(source, 'apply', '--index', '--whitespace=nowarn', str(KIT / 'port.patch'))
    epoch = (c['baseline_epoch'] if selection['upstream_commit'] == c['hardware_reference_upstream']
             else int(git(source, 'show', '-s', '--format=%ct', selection['upstream_commit'])))
    profile_path = source / 'tools/p2812/nandfinal1/profile.json'
    profile = json.loads(profile_path.read_text())
    profile.update(upstream_commit=selection['upstream_commit'], source_date_epoch=epoch)
    if c.get('builder_recipe_sha256'):
        profile.pop('builder_archive_sha256', None)
        profile.update(builder_recipe_sha256=c['builder_recipe_sha256'],
                       builder_packages_sha256=c['builder_packages_sha256'])
    write_json(profile_path, profile)
    env = dict(os.environ, GIT_AUTHOR_DATE=f'@{epoch} +0000', GIT_COMMITTER_DATE=f'@{epoch} +0000')
    git(source, 'add', '-A')
    git(source, '-c', 'user.name=P2812 CI', '-c', 'user.email=p2812-ci@users.noreply.github.com',
        '-c', 'commit.gpgsign=false', 'commit', '-q', '-m',
        f'Apply P2812HNU-F1 port to {selection["upstream_tag"]} ({selection["recipe_sha256"][:12]})', env=env)
    need(not git(source, 'status', '--porcelain'), 'Prepared source is not clean')
    selection.update(source_commit=git(source, 'rev-parse', 'HEAD'), source_date_epoch=epoch,
                     source_tree=git(source, 'rev-parse', 'HEAD^{tree}'))
    write_json(work / 'selection.json', selection)
    (work / 'port-diffstat.txt').write_text(
        git(source, 'diff', '--stat', selection['upstream_commit'], 'HEAD') + '\n')
    print('Prepared exact stable upstream + P2812 source: ' + selection['source_commit'])


def regular(path, root):
    need(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
         'Expected regular artifact missing: ' + str(path))
    return path


def package(c, work):
    selection = json.loads((work / 'selection.json').read_text())
    need(selection['recipe_sha256'] == recipe_id(), 'Build recipe changed')
    first, second = work / 'run1', work / 'run2'
    summaries = [json.loads((p / 'summary.json').read_text()) for p in (first, second)]
    repro = json.loads((second / 'reproducibility.json').read_text())
    need(all(s['passed'] for s in summaries) and repro['passed'], 'Both builds and comparison must pass')
    need(all(s['source_commit'] == selection['source_commit'] for s in summaries), 'Wrong built source')
    environment = json.loads((work / 'builder-record.json').read_text())
    need(environment['passed'] and environment['recipe_sha256'] == c['builder_recipe_sha256'] and
         environment['packages_sha256'] == c['builder_packages_sha256'], 'Environment verification differs')
    need(all(s['builder_image_id'] == environment['image_id'] and
             s['builder_recipe_sha256'] == c['builder_recipe_sha256'] and
             s['builder_record_sha256'] == sha(work / 'builder-record.json') for s in summaries),
         'Builds used a different environment')
    files = ('u-boot.bin', 'u-boot.bin.lzo', 'u-boot.uImage', 'nandfinal1-main-area.img',
             'boot-main-256k.bin', 'boot-main-oob.bin')
    assets = work / 'release-assets'
    assets.mkdir(exist_ok=False)
    hashes = {}
    for name in files:
        p = regular(first / 'results/package' / name, first / 'results')
        hashes[name] = sha(p)
        shutil.copyfile(p, assets / name)
    reference_match = hashes == c['hardware_reference_files']
    if selection['upstream_commit'] == c['hardware_reference_upstream']:
        need(reference_match, 'Baseline firmware differs from the hardware-tested reference')
    raw_source = regular(first / 'inputs/source.tar', first / 'inputs')
    with (assets / 'source.tar.gz').open('xb') as f:
        with gzip.GzipFile(filename='', mode='wb', fileobj=f, mtime=selection['source_date_epoch']) as z:
            with raw_source.open('rb') as src:
                shutil.copyfileobj(src, z)
    manifest = dict(schema=1, board='Zyxel P-2812HNU-F1', **selection,
                    build_environment=environment,
                    actual_builder_image_id=summaries[0]['builder_image_id'],
                    public_test_inputs_sha256=summaries[0]['public_inputs_sha256'],
                    independent_clean_builds=2, identical_outputs=repro['identical_outputs'],
                    firmware_sha256=hashes, matches_hardware_tested_reference=reference_match,
                    actual_hardware_test_performed_by_this_CI=False, automatic_flashing=False)
    write_json(assets / 'build-manifest.json', manifest)
    shutil.copyfile(work / 'port-diffstat.txt', assets / 'port-diffstat.txt')
    # Include the complete build/control recipe alongside the exact patched
    # firmware source, so reviewers can reproduce the environment as well.
    with zipfile.ZipFile(assets / 'ci-recipe.zip', 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for name in ('controller.py', 'config.json', 'port.patch', 'test_controller.py',
                     'builder/Dockerfile', 'builder/packages.lock'):
            z.write(regular(KIT / name, KIT), 'p2812-ci/' + name)
        for name in ('.github/workflows/p2812-upstream.yml', 'P2812-CI.md'):
            z.write(regular(KIT.parent / name, KIT.parent), name)
    with zipfile.ZipFile(assets / 'test-evidence.zip', 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for run, prefix in ((first, 'run1'), (second, 'run2')):
            z.write(regular(run / 'summary.json', run), prefix + '/summary.json')
            for name in ('audit.json', 'linuxrun-mips-tests.json', 'autoboot-tests.json',
                         'mips-tests.json', 'native-tests.json', 'main/startup-cache-tests.json'):
                z.write(regular(run / 'results' / name, run / 'results'), prefix + '/' + name)
        z.write(second / 'reproducibility.json', 'reproducibility.json')
    lines = [sha(p) + '  ' + p.name for p in sorted(assets.iterdir())]
    (assets / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')
    notes = (f'P2812HNU-F1 build of upstream **{selection["upstream_tag"]}**.\n\n'
             f'Upstream: `{selection["upstream_commit"]}`\n\n'
             f'Port recipe: `{selection["recipe_sha256"]}`\n\n'
             f'[Review upstream → P2812 source diff](https://github.com/{c["repository"]}/compare/'
             f'{selection["upstream_commit"]}...{selection["source_commit"]}). '
             '`port-diffstat.txt` lists changed files and line counts; `ci-recipe.zip` contains the build recipe.\n\n'
             f'Two clean offline builds passed; {repro["identical_outputs"]} outputs matched. '
             'SPL/TPL, ECC/LZO, startup, autoboot and Linux handoff models passed.\n\n'
             f'Matches previously hardware-tested firmware hashes: **{reference_match}**. '
             'This CI run does not execute hardware tests or flash a modem.\n\n'
             '`source.tar.gz` is the exact patched source used for the binaries. '
             '`build-manifest.json`, `test-evidence.zip` and `SHA256SUMS` record the inputs and results.\n')
    (work / 'release-notes.md').write_text(notes)
    print('Verified and packaged release assets; reference match=' + str(reference_match))


def publish(c, work):
    write_guard(c)
    selection = json.loads((work / 'selection.json').read_text())
    manifest = json.loads((work / 'release-assets/build-manifest.json').read_text())
    need(selection['recipe_sha256'] == recipe_id() == manifest['recipe_sha256'], 'Recipe mismatch')
    need(manifest['independent_clean_builds'] == 2, 'Build validation missing')
    checksums = work / 'release-assets/SHA256SUMS'
    need(checksums.is_file(), 'Release checksums missing')
    for line in checksums.read_text().splitlines():
        digest, name = line.split('  ', 1)
        need('/' not in name and sha(regular(checksums.parent / name, checksums.parent)) == digest,
             'Release asset changed after verification')
    path = f'/repos/{c["repository"]}/releases'
    release = github('GET', path + '/tags/' + selection['release_tag'], authenticated=True)
    if release and not release['draft']:
        print('Release already published; no changes')
        return
    source = work / 'source'
    need(git(source, 'rev-parse', 'HEAD') == selection['source_commit'] and
         not git(source, 'status', '--porcelain'), 'Published source must equal the built source')
    command(['gh', 'auth', 'setup-git', '--hostname', 'github.com'], capture=False)
    git(source, 'remote', 'add', 'publisher', 'https://github.com/' + c['repository'] + '.git')
    tag = selection['release_tag']
    env = dict(os.environ, GIT_COMMITTER_DATE=f'@{selection["source_date_epoch"]} +0000')
    git(source, '-c', 'user.name=P2812 CI', '-c', 'user.email=p2812-ci@users.noreply.github.com',
        'tag', '-a', tag, '-m', 'P2812 ' + selection['upstream_tag'], env=env)
    generated = 'refs/heads/p2812/builds/' + tag
    git(source, 'push', '--atomic', 'publisher', selection['source_commit'] + ':' + generated,
        selection['upstream_commit'] + ':refs/heads/upstream/stable', 'refs/tags/' + tag)
    if not release:
        release = github('POST', path, dict(tag_name=tag, draft=True, prerelease=False,
                         name='P2812HNU-F1 — U-Boot ' + selection['upstream_tag'],
                         body=(work / 'release-notes.md').read_text()), authenticated=True)
    release_path = path + '/' + str(release['id'])
    existing_assets = {a['name']: a for a in github('GET', release_path, authenticated=True)['assets']}
    for asset in sorted((work / 'release-assets').iterdir()):
        regular(asset, work / 'release-assets')
        if asset.name in existing_assets:
            github('DELETE', path + '/assets/' + str(existing_assets[asset.name]['id']), authenticated=True)
        upload = release['upload_url'].split('{', 1)[0] + '?' + urllib.parse.urlencode({'name': asset.name})
        uploaded = github('POST', upload, raw=asset.read_bytes(), authenticated=True)
        need(uploaded['size'] == asset.stat().st_size, 'Uploaded asset size mismatch')
        if uploaded.get('digest'):
            need(uploaded['digest'] == 'sha256:' + sha(asset), 'Uploaded asset digest mismatch')
    expected = {p.name for p in (work / 'release-assets').iterdir()}
    actual = {a['name'] for a in github('GET', release_path, authenticated=True)['assets']}
    need(expected == actual, 'Draft release asset list incomplete')
    release = github('PATCH', release_path, dict(draft=False, make_latest='true'), authenticated=True)
    print('Published ' + release['html_url'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=('discover', 'heartbeat', 'builder', 'prepare', 'package', 'publish'))
    p.add_argument('--work', type=Path)
    p.add_argument('--local-source', type=Path, help='Local validation source; never used by hosted workflow')
    a = p.parse_args()
    c = configuration()
    if a.operation == 'heartbeat':
        heartbeat(c)
    else:
        need(a.work, 'A fresh work directory is required')
        work = a.work.resolve()
        if a.operation == 'builder':
            build_environment(c, work)
        elif a.operation == 'prepare':
            prepare(c, work, a.local_source)
        else:
            globals()[a.operation](c, work)


if __name__ == '__main__':
    main()
