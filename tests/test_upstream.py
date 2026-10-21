"""Exercise exact-commit fetching, cache reuse and reproducible source generation."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_config', ROOT / 'scripts/build_config.py')
config_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_module)
spec = importlib.util.spec_from_file_location('upstream', ROOT / 'scripts/upstream.py')
upstream = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'build_config': config_module}):
    spec.loader.exec_module(upstream)


@unittest.skipUnless(shutil.which('git'), 'Git unavailable')
class GitSource(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / 'remote'
        self.cache = self.root / '.cache'
        subprocess.run(['git', 'init', '--quiet', str(self.repository)], check=True)
        (self.repository / 'LICENSE').write_text('test license\n')
        (self.repository / 'source.c').write_text('original source\n')
        self.git('add', '.')
        self.commit = self.make_commit(1704067200)
        self.config = config_module.load_config()
        self.config.update(UPSTREAM_REPOSITORY='test/project', UPSTREAM_COMMIT=self.commit,
                           UPSTREAM_GIT_URL=self.repository.as_uri(),
                           UPSTREAM_SOURCE_FILE=f'vlmcsd-{self.commit}.tar.gz')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repository), *args], text=True).strip()

    def make_commit(self, epoch):
        env = {**os.environ, 'GIT_AUTHOR_DATE': f'{epoch} +0000', 'GIT_COMMITTER_DATE': f'{epoch} +0000'}
        subprocess.run(['git', '-C', str(self.repository), '-c', 'user.name=Test',
                        '-c', 'user.email=test@example.invalid', 'commit', '--quiet', '-m', 'fixture'],
                       env=env, check=True)
        return self.git('rev-parse', 'HEAD')

    def prepare(self):
        return upstream.prepare_upstream(self.config, self.cache)

    def archive(self):
        return self.cache / 'upstream-sources' / self.config['UPSTREAM_SOURCE_FILE']

    def test_fetches_exact_commit_and_generates_all_metadata(self):
        lock = self.prepare()
        self.assertEqual(lock['commit'], self.commit)
        self.assertEqual(lock['source_date_epoch'], 1704067200)
        self.assertEqual(lock['version'], '20240101~' + self.commit[:12])
        self.assertEqual(lock['archive_sha256'], hashlib.sha256(self.archive().read_bytes()).hexdigest())
        with tarfile.open(self.archive()) as archive:
            member = archive.extractfile(f'vlmcsd-{self.commit}/source.c')
            self.assertEqual(member.read(), b'original source\n')

    def test_cached_commit_and_archive_need_no_fetch_or_rewrite(self):
        expected = self.prepare()
        timestamp = self.archive().stat().st_mtime_ns
        with patch.object(upstream, 'git', wraps=upstream.git) as calls:
            self.assertEqual(self.prepare(), expected)
        self.assertFalse(any('fetch' in call.args for call in calls.call_args_list))
        self.assertEqual(timestamp, self.archive().stat().st_mtime_ns)

    def test_corrupt_archive_is_regenerated_identically_without_fetch(self):
        lock = self.prepare()
        expected = self.archive().read_bytes()
        self.archive().write_bytes(b'truncated archive')
        with patch.object(upstream, 'git', wraps=upstream.git) as calls:
            self.assertEqual(self.prepare(), lock)
        self.assertFalse(any('fetch' in call.args for call in calls.call_args_list))
        self.assertEqual(self.archive().read_bytes(), expected)

    def test_changing_commit_updates_version_source_and_lock(self):
        self.prepare()
        (self.repository / 'source.c').write_text('new source\n')
        self.git('add', '.')
        new_commit = self.make_commit(1704153600)
        self.config.update(UPSTREAM_COMMIT=new_commit, UPSTREAM_SOURCE_FILE=f'vlmcsd-{new_commit}.tar.gz')
        lock = self.prepare()
        self.assertEqual(lock['version'], '20240102~' + new_commit[:12])
        self.assertEqual(lock['commit'], new_commit)
        self.assertEqual(json.loads((self.cache / 'upstream.json').read_text()), lock)

    def test_unavailable_commit_preserves_the_previous_lock_and_archive(self):
        self.prepare()
        lock = (self.cache / 'upstream.json').read_bytes()
        archive = self.archive().read_bytes()
        self.config['UPSTREAM_COMMIT'] = '0' * 40
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertEqual((self.cache / 'upstream.json').read_bytes(), lock)
        self.assertEqual(self.archive().read_bytes(), archive)

    def test_gitlinks_are_recorded_without_fetching_external_submodule_contents(self):
        self.git('update-index', '--add', '--cacheinfo', '160000', self.commit, 'submodule')
        submodule_commit = self.make_commit(1704153600)
        self.config.update(UPSTREAM_COMMIT=submodule_commit,
                           UPSTREAM_SOURCE_FILE=f'vlmcsd-{submodule_commit}.tar.gz')
        lock = self.prepare()
        self.assertEqual(lock['submodules'], {'submodule': self.commit})
        with tarfile.open(self.archive()) as archive:
            self.assertFalse(any(member.isfile() and '/submodule/' in member.name for member in archive))


if __name__ == '__main__':
    unittest.main()
