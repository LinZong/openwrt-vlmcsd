import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('manage', Path(__file__).parents[1] / 'scripts/manage.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class TransactionSafety(unittest.TestCase):
    def test_device_requirements_follow_the_selected_release(self):
        manifest = {'openwrt': 'test-release', 'arch': 'test-arch'}
        snapshot = {'board': {'DISTRIB_RELEASE': 'test-release', 'DISTRIB_ARCH': 'test-arch'},
                    'pending': '', 'available_kib': 32768, 'listening': ''}
        m.baseline_check(snapshot, manifest)
        for key in ['DISTRIB_RELEASE', 'DISTRIB_ARCH']:
            with self.subTest(key=key):
                changed = {**snapshot, 'board': {**snapshot['board'], key: 'different'}}
                with self.assertRaisesRegex(ValueError, 'requires OpenWrt test-release test-arch'):
                    m.baseline_check(changed, manifest)

    def test_modified_release_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'package.apk').write_bytes(b'original')
            manifest = {'files': [{'file': 'package.apk', 'sha256': m.digest(b'original')}]}
            (root / 'release.json').write_text(json.dumps(manifest))
            m.load_release(root)
            (root / 'package.apk').write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError, 'modified'):
                m.load_release(root)

    def test_release_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'release.json').write_text(json.dumps({'files': [{'file': '../outside', 'sha256': 'x'}]}))
            with self.assertRaises(ValueError):
                m.load_release(root)

    def test_upgrades_require_exact_old_package(self):
        s = {'installed': {'vlmcsd': {'version': 'old-r1'}}}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'Missing exact rollback'):
                m.old_artifacts({'vlmcsd'}, s, Path(d), {}, d)

    def test_transaction_path_cannot_escape(self):
        for txid in ['', '../x', '20261021T000000Z-ffffffff/..', 'x;reboot']:
            with self.assertRaises(ValueError):
                m.valid_txid(txid)

    def test_target_drift_changes_fingerprint_but_uptime_does_not(self):
        s = dict(board={}, hashes={'config': 'one'}, world='x', enabled='1', installed={}, pending='', ownership='{}')
        original = m.fingerprint(s)
        s['boot_id'] = 'new'
        s['available_kib'] = 1
        self.assertEqual(original, m.fingerprint(s))
        s['hashes']['config'] = 'two'
        self.assertNotEqual(original, m.fingerprint(s))


if __name__ == '__main__':
    unittest.main()
