# SPDX-License-Identifier: GPL-2.0-or-later
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

import make_overlay
import verify_avm


class RecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (Path(os.environ['P2812_DSL_INPUTS']) / 'dsl_control').read_bytes()

    def test_unexpected_init_is_refused(self):
        with self.assertRaises(ValueError):
            make_overlay.patch_init(self.base + b'\n')

    def test_real_autoboot_shell_modes_and_command_order(self):
        patched = make_overlay.patch_init(self.base)
        start = patched.index(b'autoboot_script() {')
        end = patched.index(b'\nlowlevel_cfg()', start)
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / 'dsl.scr'
            function = patched[start:end].decode().replace('/tmp/dsl.scr', str(output))
            for value in ['', '0', '1', '2']:
                with self.subTest(mode=value):
                    subprocess.run(['sh', '-c', function + '\nautoboot_script 0 00:11:22:33:44:55 "$1"',
                                    'test', value], check=True)
                    script = output.read_text()
                    self.assertIn('locs 0 0', script)
                    self.assertIn('dsmmcs 00:11:22:33:44:55', script)
                    if value:
                        self.assertEqual(script.splitlines()[1], 'dsmcs ' + value)
                        self.assertLess(script.index('dsmcs'), script.index('locs'))
                    else:
                        self.assertNotIn('dsmcs', script)

    def test_patched_init_shell_syntax(self):
        subprocess.run(['sh', '-n'], input=make_overlay.patch_init(self.base), check=True)

    def test_wrong_firmware_is_refused_before_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'firmware').write_bytes(b'incorrect data')
            (root / 'init').write_bytes(self.base)
            with self.assertRaises(ValueError):
                make_overlay.build(root / 'firmware', root / 'init', root / 'files')
            self.assertFalse((root / 'files').exists())

    def test_cached_input_corruption_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / 'input').write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                verify_avm.fetch({'name': 'input', 'sha256': '0' * 64, 'url': 'unused'}, cache)

    def test_source_archive_path_escape_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / 'source.tar'
            with tarfile.open(archive, 'w') as stream:
                info = tarfile.TarInfo('../escape')
                info.size = 1
                stream.addfile(info, io.BytesIO(b'x'))
            with self.assertRaises(ValueError):
                verify_avm.unpack_sources(archive, root / 'work')
            self.assertFalse((root / 'escape').exists())


if __name__ == '__main__':
    unittest.main()
