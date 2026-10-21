"""Verify cached signing metadata and runtime parallelism without HTTP requests."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_config', ROOT / 'scripts/build_config.py')
config_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_module)
spec = importlib.util.spec_from_file_location('build_helpers', ROOT / 'scripts/build_helpers.py')
helpers = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'build_config': config_module}):
    spec.loader.exec_module(helpers)


class Parallelism(unittest.TestCase):
    def test_explicit_job_count(self):
        with patch.dict(os.environ, {'BUILD_JOBS': '3'}):
            self.assertEqual(helpers.build_jobs(), 3)
        for value in ['0', '-1', '1.5', 'unlimited', '2;exit']:
            with self.subTest(value=value), patch.dict(os.environ, {'BUILD_JOBS': value}):
                with self.assertRaisesRegex(ValueError, 'positive integer'):
                    helpers.build_jobs()

    def test_detects_affinity_and_honors_container_cpu_quota(self):
        with patch.dict(os.environ, {'BUILD_JOBS': ''}), \
                patch.object(os, 'sched_getaffinity', return_value=set(range(8)), create=True), \
                patch.object(Path, 'read_text', return_value='150000 100000'):
            self.assertEqual(helpers.build_jobs(), 2)

    def test_portable_cpu_fallback(self):
        with patch.dict(os.environ, {'BUILD_JOBS': ''}), \
                patch.object(os, 'sched_getaffinity', side_effect=OSError, create=True), \
                patch.object(os, 'cpu_count', return_value=4), \
                patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            self.assertEqual(helpers.build_jobs(), 4)


@unittest.skipUnless(all(shutil.which(name) for name in ['gpg', 'gpgv', 'gpgconf']), 'GnuPG unavailable')
class MetadataCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.home = cls.root / 'gnupg'
        cls.home.mkdir(mode=0o700)
        cls.fixture = cls.root / 'fixture'
        cls.fixture.mkdir()
        cls.gpg = ['gpg', '--batch', '--homedir', str(cls.home)]
        subprocess.run(cls.gpg + ['--pinentry-mode', 'loopback', '--passphrase', '',
                                  '--quick-generate-key', 'Build cache test <cache@example.invalid>',
                                  'ed25519', 'sign', '0'], check=True, capture_output=True)
        listing = subprocess.check_output(cls.gpg + ['--with-colons', '--list-keys'], text=True)
        fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
        cls.config = config_module.load_config()
        cls.config['OPENWRT_KEY_FINGERPRINT'] = fingerprint
        cls.config['SDK_SHA256'] = 'a' * 64
        cls.files = ['openwrt-release.asc', 'sha256sums', 'sha256sums.asc']
        (cls.fixture / cls.files[0]).write_bytes(subprocess.check_output(cls.gpg + ['--armor', '--export', fingerprint]))
        (cls.fixture / 'sha256sums').write_text(f"{cls.config['SDK_SHA256']} *{cls.config['SDK_FILE']}\n")
        subprocess.run(cls.gpg + ['--yes', '--armor', '--detach-sign', '--output', str(cls.fixture / 'sha256sums.asc'),
                                  str(cls.fixture / 'sha256sums')], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['gpgconf', '--homedir', str(cls.home), '--kill', 'gpg-agent'], capture_output=True)
        cls.temporary.cleanup()

    def setUp(self):
        self.temporary_cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_cache.cleanup)
        self.cache = Path(self.temporary_cache.name)
        for name in self.files:
            shutil.copy2(self.fixture / name, self.cache / name)
        self.requests = []
        self.real_run = subprocess.run

    def fetch_fixture(self, args, **kwargs):
        if args[0] != 'curl':
            return self.real_run(args, **kwargs)
        url, _, destination = args[-3:]
        self.requests.append(url)
        name = 'openwrt-release.asc' if url == self.config['OPENWRT_KEY_URL'] else url.rsplit('/', 1)[1]
        shutil.copy2(self.fixture / name, destination)
        return subprocess.CompletedProcess(args, 0)

    def prepare(self):
        with patch.object(subprocess, 'run', side_effect=self.fetch_fixture):
            helpers.prepare_sdk_metadata(self.config, self.cache)

    def test_valid_local_files_need_no_download(self):
        before = {name: (self.cache / name).stat().st_mtime_ns for name in self.files}
        self.prepare()
        self.assertEqual(self.requests, [])
        self.assertEqual(before, {name: (self.cache / name).stat().st_mtime_ns for name in self.files})

    def test_missing_signature_only_downloads_the_signature(self):
        (self.cache / 'sha256sums.asc').unlink()
        self.prepare()
        self.assertEqual(self.requests, [self.config['SDK_URL'] + '/sha256sums.asc'])
        self.assertTrue(helpers.verify_metadata(self.config, self.cache))

    def test_corrupt_cache_is_refreshed_and_verified(self):
        (self.cache / 'sha256sums').write_bytes(b'truncated')
        self.prepare()
        self.assertEqual(len(self.requests), 3)
        self.assertTrue(helpers.verify_metadata(self.config, self.cache))

    def test_download_failure_preserves_existing_cache(self):
        (self.cache / 'sha256sums').write_bytes(b'old data')
        before = {name: (self.cache / name).read_bytes() for name in self.files}
        with patch.object(subprocess, 'run', side_effect=subprocess.CalledProcessError(7, ['curl'])):
            with self.assertRaises(subprocess.CalledProcessError):
                helpers.prepare_sdk_metadata(self.config, self.cache)
        self.assertEqual(before, {name: (self.cache / name).read_bytes() for name in self.files})

    def test_wrong_signing_key_is_rejected_without_overwriting_cache(self):
        before = {name: (self.cache / name).read_bytes() for name in self.files}
        self.config = {**self.config, 'OPENWRT_KEY_FINGERPRINT': '0' * 40}
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            self.prepare()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(before, {name: (self.cache / name).read_bytes() for name in self.files})


if __name__ == '__main__':
    unittest.main()
