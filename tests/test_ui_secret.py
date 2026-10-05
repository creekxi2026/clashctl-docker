"""Exercise entrypoint secret storage with real files; stub only external yq."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = 'fixture-only-7d89e4c06a315bf291e836b7c94da250'


class UISecretTests(unittest.TestCase):
    def setup_secret(self, folder, value=None, previous=''):
        source = (ROOT / 'container/entrypoint').read_text().split("<<'PY'\n", 1)[1].split('\nPY', 1)[0]
        source = source.replace('/data/controller.secret', str(folder / 'controller.secret'))
        env = {} if value is None else {'UI_SECRET': value}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(subprocess, 'check_output', return_value=previous), \
                patch.object(subprocess, 'run'):
            exec(compile(source, '<controller-setup>', 'exec'), {})

    def test_explicit_secret_is_selected_and_private(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            folder = Path(tmp)
            self.setup_secret(folder, FIXTURE, previous='older-mixin-secret')
            path = folder / 'controller.secret'
            self.assertEqual(path.read_text(), FIXTURE)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_atomic_write_failure_preserves_existing_secret(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            folder = Path(tmp)
            path = folder / 'controller.secret'
            path.write_text(FIXTURE)
            with patch.object(os, 'replace', side_effect=OSError('fixture write failure')):
                with self.assertRaisesRegex(OSError, 'fixture write failure'):
                    self.setup_secret(folder, FIXTURE + '-rotated')
            self.assertEqual(path.read_text(), FIXTURE)
            self.assertEqual(list(folder.iterdir()), [path])

    def test_rotation_recreation_and_unset_or_empty_retention(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            folder = Path(tmp)
            self.setup_secret(folder, FIXTURE)
            rotated = FIXTURE + '-rotated'
            for value in (rotated, rotated, None, ''):
                self.setup_secret(folder, value)
                self.assertEqual((folder / 'controller.secret').read_text(), rotated)

    def test_existing_empty_file_rejected_even_with_explicit_secret(self):
        for content in ('', ' \n'):
            with self.subTest(content=content), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
                folder = Path(tmp)
                (folder / 'controller.secret').write_text(content)
                with self.assertRaisesRegex(SystemExit, 'must not be empty'):
                    self.setup_secret(folder, FIXTURE)
                self.assertEqual((folder / 'controller.secret').read_text(), content)

    def test_invalid_explicit_secret_does_not_change_stored_secret(self):
        for value in (' ', FIXTURE + '\n', 'prefix\rvalue', 'embedded space', '\x7f'):
            with self.subTest(value=repr(value)), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
                folder = Path(tmp)
                path = folder / 'controller.secret'
                path.write_text(FIXTURE)
                with self.assertRaisesRegex(SystemExit, 'UI_SECRET'):
                    self.setup_secret(folder, value)
                self.assertEqual(path.read_text(), FIXTURE)

    def test_symlinks_rejected_without_changing_target(self):
        for dangling in (False, True):
            with self.subTest(dangling=dangling), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
                folder = Path(tmp)
                target = folder / 'target'
                if not dangling:
                    target.write_text(FIXTURE)
                    target.chmod(0o644)
                (folder / 'controller.secret').symlink_to(target)
                with self.assertRaisesRegex(SystemExit, 'must not be a symlink'):
                    self.setup_secret(folder, FIXTURE + '-changed')
                if not dangling:
                    self.assertEqual(target.read_text(), FIXTURE)
                    self.assertEqual(target.stat().st_mode & 0o777, 0o644)
                else:
                    self.assertFalse(target.exists())

    def test_nonregular_files_rejected(self):
        for kind in ('directory', 'fifo'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
                folder = Path(tmp)
                path = folder / 'controller.secret'
                path.mkdir() if kind == 'directory' else os.mkfifo(path)
                with self.assertRaisesRegex(SystemExit, 'regular file'):
                    self.setup_secret(folder, FIXTURE)

    def test_unset_and_empty_preserve_mixin_or_generate(self):
        for value in (None, ''):
            for previous in ('', FIXTURE):
                with self.subTest(value=value, previous=bool(previous)), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
                    folder = Path(tmp)
                    self.setup_secret(folder, value, previous)
                    path = folder / 'controller.secret'
                    first = path.read_text()
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(first, previous) if previous else self.assertGreaterEqual(len(first), 32)
                    self.setup_secret(folder, value)
                    self.assertEqual(path.read_text(), first)


if __name__ == '__main__':
    unittest.main()
