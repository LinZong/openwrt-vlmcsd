"""ARM64 ABI selection, cache isolation and architecture-safe recovery."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_config as config
import manage

TARGETS = ['x86/64', 'armsr/armv8', 'mediatek/filogic', 'rockchip/armv8']


class Arm64(unittest.TestCase):
    def test_all_twelve_combinations_have_isolated_sdk_and_release_paths(self):
        matrix = [config.load_config(profile=p, target=t) for p in ['23.05', '24.10', '25.12'] for t in TARGETS]
        for key in ['SDK_PATH', 'SDK_METADATA_PATH', 'SDK_ARCHIVE_PATH', 'RELEASE_PATH', 'VERIFICATION_PATH', 'REPORT_SUFFIX']:
            self.assertEqual(len({c[key] for c in matrix}), 12, key)
        for c in matrix:
            if c['OPENWRT_TARGET'] != 'x86/64':
                self.assertEqual(c['ELF_MACHINE'], 'AArch64')
                self.assertEqual(c['ELF_INTERPRETER'], '/lib/ld-musl-aarch64.so.1')
            self.assertEqual(c['BUILDER_PLATFORM'], 'linux/amd64')
            self.assertIn('Linux-x86_64', c['SDK_FILE'])

    def test_cli_target_precedes_environment(self):
        with patch.dict(os.environ, OPENWRT_TARGET='mediatek/filogic'):
            self.assertEqual(config.load_config()['OPENWRT_ARCH'], 'aarch64_cortex-a53')
            c = config.load_config(target='armsr/armv8')
            self.assertEqual(c['OPENWRT_ARCH'], 'aarch64_generic')
            output = subprocess.check_output([sys.executable, str(ROOT / 'scripts/build_config.py'), 'json', '--target', 'rockchip/armv8'], text=True)
            self.assertEqual(json.loads(output)['OPENWRT_TARGET'], 'rockchip/armv8')

    def test_frozen_arm_snapshot_ignores_both_selectors(self):
        c = config.load_config(profile='23.05', target='armsr/armv8')
        frozen = {k: int(c[k]) if k in config.INTEGER_KEYS else c[k]
                  for k in config.STRING_KEYS | config.INTEGER_KEYS | (config.OPTIONAL_KEYS & c.keys())}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'build-config.json'
            p.write_text(json.dumps(frozen))
            with patch.dict(os.environ, OPENWRT_PROFILE='25.12', OPENWRT_TARGET='x86/64'):
                actual = config.load_config(p, profile='24.10', target='mediatek/filogic')
            self.assertEqual(actual['OPENWRT_TARGET'], 'armsr/armv8')
            self.assertEqual(actual['OPENWRT_VERSION'], '23.05.6')
            self.assertEqual(actual['VM_KERNEL_SHA256'], c['VM_KERNEL_SHA256'])

    def test_unknown_target_and_mismatched_abi_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown OpenWrt target'):
            config.load_config(target='arm/generic')
        source = json.loads(config.CONFIG.read_text())
        source['OPENWRT_TARGETS']['armsr/armv8']['OPENWRT_ARCH'] = 'aarch64_cortex-a53'
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'build.json'; p.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, 'mismatched'):
                config.load_config(p, target='armsr/armv8')

    def test_same_abi_different_board_is_rejected_by_transaction(self):
        manifest = {'openwrt': '25.12.1', 'arch': 'aarch64_generic', 'target': 'rockchip/armv8'}
        s = {'board': {'DISTRIB_RELEASE': '25.12.1', 'DISTRIB_ARCH': 'aarch64_generic', 'DISTRIB_TARGET': 'armsr/armv8'}}
        with self.assertRaisesRegex(ValueError, 'requires OpenWrt target'):
            manage.baseline_check(s, manifest)

    def test_legacy_manifests_cannot_authorize_arm(self):
        with self.assertRaisesRegex(ValueError, 'Legacy'):
            manage.baseline_check({'board': {'DISTRIB_RELEASE': '25.12.1', 'DISTRIB_ARCH': 'aarch64_generic'}},
                                  {'openwrt': '25.12.1', 'arch': 'aarch64_generic'})

    def test_rollback_reads_metadata_and_skips_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as d:
            release = Path(d) / 'release'; cache = Path(d) / 'cache'
            release.mkdir(); cache.mkdir()
            name = 'vlmcsd-old-r1.apk'
            (release / name).write_bytes(b'x86'); (cache / name).write_bytes(b'arm')
            s = {'installed': {'vlmcsd': {'version': 'old-r1'}}}
            def info(path, release):
                return {'name': 'vlmcsd', 'version': 'old-r1', 'arch': 'x86_64' if path.read_bytes() == b'x86' else 'aarch64_generic'}
            with patch.object(manage, 'apk_metadata', side_effect=info):
                result = manage.old_artifacts({'vlmcsd'}, s, release, {'arch': 'aarch64_generic'}, cache)
                self.assertEqual(result[0]['file'], str((cache / name).resolve()))
                (cache / name).unlink()
                with self.assertRaisesRegex(ValueError, 'architecture mismatch'):
                    manage.old_artifacts({'vlmcsd'}, s, release, {'arch': 'aarch64_generic'}, cache)

    def test_owned_dependency_rollback_uses_installed_arch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'helper-old-r1.apk').write_bytes(b'package')
            snapshot = {'installed': {'helper': {'version': 'old-r1', 'arch': 'aarch64_generic'}}}
            with patch.object(manage, 'apk_metadata', return_value={'name': 'helper', 'version': 'old-r1', 'arch': 'aarch64_generic'}):
                result = manage.old_artifacts({'helper'}, snapshot, root, {'arch': 'aarch64_generic'}, root)
                self.assertEqual(result[0]['name'], 'helper')
            with patch.object(manage, 'apk_metadata', return_value={'name': 'helper', 'version': 'old-r1', 'arch': 'noarch'}):
                with self.assertRaisesRegex(ValueError, 'architecture mismatch'):
                    manage.old_artifacts({'helper'}, snapshot, root, {'arch': 'aarch64_generic'}, root)

    def test_explicit_release_does_not_require_current_build_config(self):
        with patch.object(sys, 'argv', ['manage.py', 'deploy', 'plan', '--release', '/frozen/release']), \
                patch.object(manage, 'load_config', side_effect=AssertionError('Do not resolve current build')), \
                patch.object(manage, 'make_plan') as plan:
            manage.main()
            self.assertEqual(plan.call_args.args[0].release, '/frozen/release')


if __name__ == '__main__':
    unittest.main()
