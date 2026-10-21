"""Release format, profile isolation, native IPK and deployment boundary regressions."""
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_config
import assemble
import ipk
import manage


def tar_bytes(files, uid=0):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, value in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            entry.uid = uid
            archive.addfile(entry, io.BytesIO(value))
    return buffer.getvalue()


def ar_bytes(files):
    data = b'!<arch>\n'
    for name, value in files.items():
        header = f'{name + "/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(value):<10}`\n'.encode()
        data += header + value + (b'\n' if len(value) % 2 else b'')
    return data


class Compatibility(unittest.TestCase):
    def test_profiles_select_native_tools_and_isolate_caches(self):
        configs = [build_config.load_config(profile=p) for p in ['23.05', '24.10', '25.12']]
        self.assertEqual([c['PACKAGE_MANAGER'] for c in configs], ['opkg', 'opkg', 'apk'])
        self.assertEqual([c['SDK_COMPRESSION'] for c in configs], ['xz', 'zst', 'zst'])
        for key in ['SDK_PATH', 'SDK_ARCHIVE_PATH', 'SDK_METADATA_PATH', 'RELEASE_PATH']:
            self.assertEqual(len({c[key] for c in configs}), 3, key)
        for key in ['UPSTREAM_REPOSITORY', 'UPSTREAM_COMMIT', 'UPSTREAM_ARCHIVE_PATH']:
            self.assertEqual(len({c[key] for c in configs}), 1, key)
        self.assertEqual(configs[2]['RELEASE_PATH'], 'dist/release')
        with self.assertRaisesRegex(ValueError, 'Unknown OpenWrt profile'):
            build_config.load_config(profile='22.03')

    def test_profile_cannot_silently_select_another_series(self):
        source = json.loads(build_config.CONFIG.read_text())
        source['OPENWRT_PROFILES']['23.05']['OPENWRT_VERSION'] = '24.10.6'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'build.json'
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, 'Profile name does not match'):
                build_config.load_config(path, profile='23.05')

    def test_snapshot_ignores_environment_profile_and_keeps_native_version(self):
        c = build_config.load_config(profile='23.05')
        snapshot = {key: int(c[key]) if key in build_config.INTEGER_KEYS else c[key]
                    for key in build_config.STRING_KEYS | build_config.INTEGER_KEYS}
        snapshot.update(UPSTREAM_VERSION='20240101~' + c['UPSTREAM_COMMIT'][:12],
                        UPSTREAM_SHA256='a' * 64, SOURCE_DATE_EPOCH=1704067200)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'build-config.json'
            p.write_text(json.dumps(snapshot))
            with patch.dict(os.environ, OPENWRT_PROFILE='25.12'):
                result = build_config.load_config(p, require_upstream=True)
            self.assertEqual(result['OPENWRT_VERSION'], c['OPENWRT_VERSION'])
            self.assertEqual(result['PACKAGE_VERSION'], snapshot['UPSTREAM_VERSION'] + '-1')
            self.assertEqual(result['PACKAGE_FORMAT'], 'ipk')

    def test_tar_and_ar_ipks_report_real_control_and_payload(self):
        control = b'Package: luci-app-vlmcsd\nVersion: 20240101~abcdef-1\nArchitecture: all\nDepends: vlmcsd, rpcd-mod-ucode (>= 2023)\nDescription: first\n continuation\n'
        files = {'debian-binary': b'2.0\n', 'control.tar.gz': tar_bytes({'./control': control}),
                 'data.tar.gz': tar_bytes({'./usr/share/test': b'payload'})}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.ipk'
            for encode in [tar_bytes, ar_bytes]:
                with self.subTest(container=encode.__name__):
                    path.write_bytes(encode(files))
                    self.assertEqual(ipk.metadata(path), {'name': 'luci-app-vlmcsd', 'version': '20240101~abcdef-1',
                                     'arch': 'all', 'depends': ['vlmcsd', 'rpcd-mod-ucode (>= 2023)']})
                    self.assertEqual(ipk.payload_file(path, '/usr/share/test'), b'payload')
                    with self.assertRaisesRegex(ValueError, 'Missing IPK payload'):
                        ipk.payload_file(path, '/etc/nonexistent')
            path.write_bytes(ar_bytes({'debian-binary': b'3.0\n', **{k: v for k,v in files.items() if k != 'debian-binary'}}))
            with self.assertRaisesRegex(ValueError, 'Invalid native IPK'):
                ipk.metadata(path)

    def test_native_release_rejects_leaked_builder_ownership(self):
        version = '20240101~abcdef-1'
        control = f'Package: vlmcsd\nVersion: {version}\nArchitecture: x86_64\n'.encode()
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory)
            packages = sdk / 'bin/packages/x86_64/base'
            packages.mkdir(parents=True)
            package = packages / 'vlmcsd.ipk'
            for uid in [0, 501]:
                package.write_bytes(tar_bytes({'debian-binary': b'2.0\n',
                    'control.tar.gz': tar_bytes({'./control': control}),
                    'data.tar.gz': tar_bytes({'./usr/sbin/vlmcsd': b'executable'}, uid=uid)}))
                with patch.object(assemble, 'SDK', sdk), patch.object(assemble, 'APP_NAMES', {'vlmcsd'}):
                    config = {'PACKAGE_FORMAT': 'ipk', 'PACKAGE_VERSION': version, 'OPENWRT_ARCH': 'x86_64'}
                    if uid:
                        with self.assertRaisesRegex(RuntimeError, 'host ownership'):
                            assemble.built_packages(config)
                    else:
                        self.assertEqual(set(assemble.built_packages(config)), {'vlmcsd'})

    def test_ipk_plan_is_rejected_before_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'release.json').write_text(json.dumps({'package_format': 'ipk', 'files': []}))
            class Args:
                release = root
            with patch.object(manage, 'snapshot', side_effect=AssertionError('Must not contact router')):
                with self.assertRaisesRegex(ValueError, 'native opkg'):
                    manage.make_plan(Args())


if __name__ == '__main__':
    unittest.main()
