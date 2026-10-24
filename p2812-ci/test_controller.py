#!/usr/bin/env python3
"""Offline control-plane tests; no Docker, network, GitHub writes or devices."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import controller as c


class StableSelection(unittest.TestCase):
    def test_stable_order_and_annotated_peel(self):
        refs = '\n'.join(['a'*40+'\trefs/tags/v2026.07', 'b'*40+'\trefs/tags/v2026.10',
                          'c'*40+'\trefs/tags/v2026.10^{}', 'd'*40+'\trefs/tags/v2027.01-rc1'])
        self.assertEqual(c.latest_stable(refs), ('v2026.10', 'c'*40))
        self.assertEqual(c.latest_stable(refs+'\n'+'e'*40+'\trefs/tags/v2026.10.1'),
                         ('v2026.10.1', 'e'*40))

    def test_rc_only_refused(self):
        with self.assertRaises(RuntimeError):
            c.latest_stable('a'*40+'\trefs/tags/v2027.01-rc1')

    def test_existing_release_skips_and_draft_rebuilds(self):
        cfg = c.configuration()
        for draft in (False, True):
            with tempfile.TemporaryDirectory() as td:
                work = Path(td) / 'work'
                with patch.object(c, 'command', return_value='a'*40+'\trefs/tags/v2026.10'), \
                     patch.object(c, 'github', return_value={'draft': draft}), patch.dict(os.environ, {}, clear=True):
                    c.discover(cfg, work)
                self.assertEqual(json.loads((work/'selection.json').read_text())['build_required'], draft)

    def test_api_failure_is_not_missing_release(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(c, 'command', return_value='a'*40+'\trefs/tags/v2026.10'), \
                 patch.object(c, 'github', side_effect=RuntimeError('HTTP403')):
                with self.assertRaises(RuntimeError):
                    c.discover(c.configuration(), Path(td)/'work')


class Guards(unittest.TestCase):
    def test_local_or_wrong_repository_write_refused(self):
        cfg = c.configuration()
        for env in ({}, {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'other/repo','GITHUB_REF':'refs/heads/main'},
                    {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':cfg['repository'],'GITHUB_REF':'refs/heads/other'}):
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError):
                    c.write_guard(cfg)

    def test_changed_patch_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = copy.deepcopy(c.configuration())
            (root/'config.json').write_text(json.dumps(cfg))
            (root/'port.patch').write_text('altered patch')
            with patch.object(c, 'KIT', root):
                with self.assertRaises(RuntimeError):
                    c.configuration()

    def test_artifact_symlink_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td);(root/'out').mkdir();(root/'secret').write_text('synthetic')
            (root/'out/link').symlink_to(root/'secret')
            with self.assertRaises(RuntimeError):
                c.regular(root/'out/link', root/'out')


class Preparation(unittest.TestCase):
    def test_ignored_added_sources_are_in_the_committed_archive(self):
        # A real local Git round trip catches lost SPL assembly/vendor patches;
        # no network or remote API is used. Simulate a Mac ignorecase checkout.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); upstream = root/'upstream'; upstream.mkdir()
            def git(*args):
                return c.command(['git', '-C', str(upstream), *args])
            git('init', '-q')
            git('config', 'user.name', 'Synthetic test')
            git('config', 'user.email', 'test@example.invalid')
            git('config', 'core.ignorecase', 'true')
            (upstream/'.gitignore').write_text('*.s\n*.patch\n')
            git('add', '.gitignore');git('-c','commit.gpgsign=false','commit','-qm','Synthetic upstream')
            oid = git('rev-parse', 'HEAD')
            files = {'tools/p2812/early/start.S':'synthetic assembly\n',
                     'tools/p2812/early/vendor-changes.patch':'synthetic vendor diff\n',
                     'tools/p2812/nandfinal1/profile.json':'{}\n'}
            for name,data in files.items():
                path=upstream/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data)
            git('add','--force',*files)
            kit=root/'recipe';kit.mkdir()
            (kit/'port.patch').write_text(git('diff','--cached','--binary')+'\n')
            (kit/'config.json').write_text('{}');(kit/'controller.py').write_text('synthetic recipe\n')
            (kit/'builder').mkdir()
            (kit/'builder/Dockerfile').write_text('synthetic Dockerfile\n')
            (kit/'builder/packages.lock').write_text('synthetic package inventory\n')
            work=root/'work';work.mkdir()
            with patch.object(c,'KIT',kit):
                c.write_json(work/'selection.json',dict(upstream_tag='v2026.10',upstream_commit=oid,
                             recipe_sha256=c.recipe_id()))
                c.prepare(dict(hardware_reference_upstream=oid,baseline_epoch=1791504000),work,upstream)
            committed=set(c.git(work/'source','ls-tree','-r','--name-only','HEAD').splitlines())
            self.assertTrue(set(files).issubset(committed))
            for name in files:
                subprocess.run(['git','-C',str(work/'source'),'cat-file','-e','HEAD:'+name],check=True)


class Packaging(unittest.TestCase):
    def fixture(self, root):
        cfg = copy.deepcopy(c.configuration())
        selection = dict(upstream_tag='v2026.10', upstream_commit=cfg['hardware_reference_upstream'],
                         recipe_sha256=c.recipe_id(), release_tag='synthetic-test-release',
                         source_commit='b'*40, source_tree='c'*40, source_date_epoch=1791504000,
                         build_required=True)
        c.write_json(root/'selection.json', selection)
        c.write_json(root/'builder-record.json',dict(passed=True,image_id='synthetic-image',
                     recipe_sha256=cfg['builder_recipe_sha256'],packages_sha256=cfg['builder_packages_sha256']))
        (root/'port-diffstat.txt').write_text('Synthetic port diffstat\n')
        for name in ('run1','run2'):
            run=root/name;(run/'results/package').mkdir(parents=True);(run/'results/main').mkdir()
            (run/'inputs').mkdir()
            summary=dict(passed=True, source_commit=selection['source_commit'],
                         builder_image_id='synthetic-image', public_inputs_sha256={},
                         builder_recipe_sha256=cfg['builder_recipe_sha256'],
                         builder_record_sha256=c.sha(root/'builder-record.json'))
            c.write_json(run/'summary.json', summary)
            for file in cfg['hardware_reference_files']:
                (run/'results/package'/file).write_bytes(('synthetic-'+file).encode())
            for file in ('audit.json','linuxrun-mips-tests.json','autoboot-tests.json',
                         'mips-tests.json','native-tests.json','main/startup-cache-tests.json'):
                c.write_json(run/'results'/file, {'passed':True,'synthetic':True})
            (run/'inputs/source.tar').write_bytes(b'SYNTHETIC SOURCE FIXTURE')
        c.write_json(root/'run2/reproducibility.json', {'passed':True,'identical_outputs':27})
        cfg['hardware_reference_files']={n:c.sha(root/'run1/results/package'/n)
                                         for n in cfg['hardware_reference_files']}
        return cfg

    def test_failed_build_cannot_package(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=self.fixture(root)
            s=json.loads((root/'run2/summary.json').read_text());s['passed']=False
            c.write_json(root/'run2/summary.json',s)
            with self.assertRaises(RuntimeError):c.package(cfg,root)
            self.assertFalse((root/'release-assets').exists())

    def test_reference_mismatch_cannot_pass_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=self.fixture(root);cfg['hardware_reference_files']['u-boot.bin']='0'*64
            with self.assertRaises(RuntimeError):c.package(cfg,root)

    def test_environment_mismatch_cannot_package(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=self.fixture(root)
            record=json.loads((root/'builder-record.json').read_text());record['packages_sha256']='0'*64
            c.write_json(root/'builder-record.json',record)
            with self.assertRaises(RuntimeError):c.package(cfg,root)
            self.assertFalse((root/'release-assets').exists())

    def test_successful_package_has_exact_checksums_and_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=self.fixture(root);c.package(cfg,root)
            manifest=json.loads((root/'release-assets/build-manifest.json').read_text())
            self.assertTrue(manifest['matches_hardware_tested_reference'])
            self.assertFalse(manifest['actual_hardware_test_performed_by_this_CI'])
            self.assertFalse(manifest['automatic_flashing'])
            for line in (root/'release-assets/SHA256SUMS').read_text().splitlines():
                digest,name=line.split('  ',1);self.assertEqual(digest,c.sha(root/'release-assets'/name))

    def test_new_upstream_does_not_claim_hardware_equivalence(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=self.fixture(root)
            s=json.loads((root/'selection.json').read_text());s['upstream_commit']='d'*40
            c.write_json(root/'selection.json',s);cfg['hardware_reference_files']={}
            c.package(cfg,root)
            self.assertFalse(json.loads((root/'release-assets/build-manifest.json').read_text())['matches_hardware_tested_reference'])


class Heartbeat(unittest.TestCase):
    def test_recent_no_write_old_only_metadata_write(self):
        cfg=c.configuration();env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':cfg['repository'],
                                   'GITHUB_REF':'refs/heads/main','GH_TOKEN':'synthetic'}
        for days,writes in ((1,False),(30,True)):
            stamp=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
            existing={'sha':'a'*40,'content':base64.b64encode(json.dumps({'last_heartbeat_utc':stamp}).encode()).decode()}
            with patch.dict(os.environ,env,clear=True),patch.object(c,'github',return_value=existing) as api:
                c.heartbeat(cfg)
            puts=[x for x in api.call_args_list if x.args[0]=='PUT']
            self.assertEqual(bool(puts),writes)
            if writes:self.assertEqual(puts[0].args[1],'/repos/'+cfg['repository']+'/contents/.p2812-ci-heartbeat')


class BuildEnvironment(unittest.TestCase):
    def exercise(self, root, packages=None, failed=False):
        cfg=c.configuration()
        with patch.object(c.subprocess,'run',return_value=c.subprocess.CompletedProcess([],1 if failed else 0)), \
             patch.object(c,'command',return_value='sha256:'+'a'*64), \
             patch.object(c.subprocess,'check_output',return_value=packages if packages is not None else (c.KIT/'builder/packages.lock').read_bytes()):
            c.build_environment(cfg,root)

    def test_matching_package_inventory_records_source_built_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.exercise(root)
            r=json.loads((root/'builder-record.json').read_text())
            self.assertTrue(r['passed'] and r['source_built'])
            self.assertFalse(r['private_environment_archive_used'])

    def test_wrong_inventory_never_creates_verified_record(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with self.assertRaises(RuntimeError):self.exercise(root,b'changed package versions\n')
            self.assertFalse((root/'builder-record.json').exists())

    def test_failed_environment_build_never_creates_verified_record(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with self.assertRaises(RuntimeError):self.exercise(root,failed=True)
            self.assertFalse((root/'builder-record.json').exists())


class Publication(unittest.TestCase):
    def run_mock_publication(self, root, fail_upload=False, published=False, tampered=False):
        cfg=Packaging().fixture(root);c.package(cfg,root)
        if tampered:(root/'release-assets/u-boot.bin').write_bytes(b'changed-after-checksums')
        state={'published':False,'assets':[],'uploads':0}
        base='/repos/'+cfg['repository']+'/releases'
        release={'id':1,'draft':True,'upload_url':'https://uploads.github.com'+base+'/1/assets{?name,label}'}
        def api(method,path,payload=None,authenticated=False,raw=None):
            self.assertTrue(authenticated)
            if method=='GET' and '/tags/' in path:
                return {'draft':False} if published else None
            if method=='POST' and path==base:return dict(release)
            if method=='GET' and path==base+'/1':return dict(release,assets=list(state['assets']))
            if method=='POST' and path.startswith('https://uploads.github.com'):
                state['uploads']+=1
                if fail_upload and state['uploads']==2:raise RuntimeError('Synthetic upload failure')
                name=c.urllib.parse.parse_qs(c.urllib.parse.urlparse(path).query)['name'][0]
                asset={'id':state['uploads'],'name':name,'size':len(raw),
                       'digest':'sha256:'+hashlib.sha256(raw).hexdigest()}
                state['assets'].append(asset);return asset
            if method=='PATCH' and path==base+'/1':
                self.assertEqual(payload,{'draft':False,'make_latest':'true'})
                state['published']=True
                return {'html_url':'https://github.com/'+cfg['repository']+'/releases/tag/synthetic'}
            raise AssertionError('Unexpected synthetic API call '+method+' '+path)
        def fake_git(source,*args,**kwargs):
            return 'b'*40 if args==('rev-parse','HEAD') else ''
        env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':cfg['repository'],
             'GITHUB_REF':'refs/heads/main','GH_TOKEN':'synthetic'}
        with patch.dict(os.environ,env,clear=True),patch.object(c,'github',side_effect=api) as remote, \
             patch.object(c,'git',side_effect=fake_git) as vcs,patch.object(c,'command') as commands:
            if fail_upload or tampered:
                with self.assertRaises(RuntimeError):c.publish(cfg,root)
            else:c.publish(cfg,root)
            state.update(api_calls=remote.call_count,git_calls=vcs.call_count,command_calls=commands.call_count)
        return state

    def test_assets_complete_before_draft_is_published(self):
        with tempfile.TemporaryDirectory() as td:
            state=self.run_mock_publication(Path(td))
            self.assertTrue(state['published']);self.assertEqual(state['uploads'],12)

    def test_upload_error_never_publishes_draft(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(self.run_mock_publication(Path(td),fail_upload=True)['published'])

    def test_existing_published_release_is_immutable(self):
        with tempfile.TemporaryDirectory() as td:
            state=self.run_mock_publication(Path(td),published=True)
            self.assertEqual(state['uploads'],0);self.assertEqual(state['git_calls'],0)

    def test_changed_artifact_stops_before_any_remote_call(self):
        with tempfile.TemporaryDirectory() as td:
            state=self.run_mock_publication(Path(td),tampered=True)
            self.assertEqual(state['api_calls'],0);self.assertFalse(state['published'])


if __name__ == '__main__':
    unittest.main()
