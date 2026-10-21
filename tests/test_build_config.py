"""Exercise config upgrades across the actual shell and package Make consumers."""
import importlib.util
import json
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


class BuildConfiguration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'build.json'
        self.values = json.loads(config_module.CONFIG.read_text())

    def write(self):
        self.path.write_text(json.dumps(self.values))
        return config_module.load_config(self.path)

    def test_upgrade_propagates_to_shell_and_both_package_makefiles(self):
        self.values.update(OPENWRT_VERSION='99.1.2', UPSTREAM_VERSION='9999',
                           UPSTREAM_COMMIT='a' * 40, UPSTREAM_SHA256='b' * 64,
                           PACKAGE_RELEASE=7, SOURCE_DATE_EPOCH=1800000000)
        config = self.write()
        result = subprocess.check_output(
            ['bash', '-eu', '-c', config_module.render(config, 'shell') +
             'printf "%s\\n" "$PACKAGE_VERSION" "$VLMCSD_BUILD_VERSION" "$SDK_FILE" "$VM_URL"'], text=True)
        self.assertEqual(result.splitlines()[:2], ['9999-r7', '9999-openwrt-r7-aaaaaaa'])
        self.assertIn('openwrt-sdk-99.1.2-', result)
        self.assertIn('/releases/99.1.2/targets/', result)
        if not shutil.which('make'):
            self.skipTest('make is unavailable')
        (self.root / 'include').mkdir()
        (self.root / 'feeds/luci').mkdir(parents=True)
        for name in ['rules.mk', 'include/package.mk', 'feeds/luci/luci.mk']:
            (self.root / name).touch()
        (self.root / 'vlmcsd-build.mk').write_text(config_module.render(config, 'make'))
        probe = self.root / 'probe.mk'
        probe.write_text("probe:\n\t@printf '%s\\n' '$(PKG_VERSION)-r$(PKG_RELEASE)' '$(PKG_SOURCE_VERSION)' '$(PKG_HASH)' '$(MAKE_FLAGS)' '$(PKG_PO_VERSION)' '$(SOURCE_DATE_EPOCH)' '$(PKG_FILE_DEPENDS)'\n")
        for package in ['vlmcsd', 'luci-app-vlmcsd']:
            with self.subTest(package=package):
                output = subprocess.check_output([
                    'make', '--no-print-directory', '-f', str(ROOT / 'package' / package / 'Makefile'),
                    '-f', str(probe), f'TOPDIR={self.root}', f'INCLUDE_DIR={self.root / "include"}', 'probe'], text=True).splitlines()
                self.assertEqual(output[0], '9999-r7')
                self.assertEqual(output[5], '1800000000')
                self.assertIn(str(self.root / 'vlmcsd-build.mk'), output[6])
                if package == 'vlmcsd':
                    self.assertEqual(output[1:3], ['a' * 40, 'b' * 64])
                    self.assertIn('9999-openwrt-r7-aaaaaaa', output[3])
                else:
                    self.assertEqual(output[4], '9999-r7')

    def test_rejects_bad_hashes_and_shell_or_make_expressions(self):
        for key, value in [('SDK_SHA256', 'abc'), ('UPSTREAM_COMMIT', 'z' * 40),
                           ('UPSTREAM_VERSION', '$(touch /tmp/should-not-run)'),
                           ('UPSTREAM_VERSION', 'version#comment'),
                           ('PACKAGE_RELEASE', True), ('SOURCE_DATE_EPOCH', -1),
                           ('CONTAINER_BASE_IMAGE', 'ubuntu:latest')]:
            with self.subTest(key=key, value=value):
                self.values = json.loads(config_module.CONFIG.read_text())
                self.values[key] = value
                with self.assertRaises(ValueError):
                    self.write()

    def test_hexadecimal_case_does_not_change_downloads_or_key_lookup(self):
        expected = config_module.load_config()
        for key in ['SDK_SHA256', 'UPSTREAM_SHA256', 'VM_IMAGE_SHA256',
                    'UPSTREAM_COMMIT', 'OPENWRT_KEY_FINGERPRINT']:
            self.values[key] = self.values[key].swapcase()
        self.assertEqual(self.write(), expected)

    def test_sdk_must_match_exact_signed_checksum_entry(self):
        config = self.write()
        sums = self.root / 'sha256sums'
        command = [sys.executable, str(ROOT / 'scripts/build_config.py'), 'json',
                   '--config', str(self.path), '--sdk-checksums', str(sums)]
        for prefix in ['', '*']:
            sums.write_text(f"{config['SDK_SHA256']} {prefix}{config['SDK_FILE']}\n")
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        for line in [f"{config['SDK_SHA256']}  {config['SDK_FILE']}.other\n",
                     f"{'0' * 64}  {config['SDK_FILE']}\n"]:
            sums.write_text(line)
            result = subprocess.run(command, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')

    def test_vm_rejects_a_cached_guest_from_a_different_image(self):
        (self.root / 'config').mkdir()
        (self.root / 'scripts').mkdir()
        (self.root / '.cache').mkdir()
        shutil.copy2(config_module.CONFIG, self.root / 'config/build.json')
        for name in ['vm.sh', 'vm-prepare.sh', 'build_config.py']:
            shutil.copy2(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        (self.root / '.cache/openwrt.img').touch()
        (self.root / '.cache/vm-cache-key').write_text('a-different-image')
        result = subprocess.run(['bash', str(self.root / 'scripts/vm.sh'), 'start'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('vm.sh reset', result.stderr)
        self.assertTrue((self.root / '.cache/openwrt.img').exists())

    def test_assembly_ignores_stale_apks_after_a_config_upgrade(self):
        spec = importlib.util.spec_from_file_location('assemble', ROOT / 'scripts/assemble.py')
        assemble = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'build_config': config_module}):
            spec.loader.exec_module(assemble)
        self.values.update(UPSTREAM_VERSION='9999', PACKAGE_RELEASE=7)
        config = self.write()
        packages = self.root / 'bin/packages/test/base'
        packages.mkdir(parents=True)
        entries = {}
        for name in assemble.APP_NAMES:
            arch = config['OPENWRT_ARCH'] if name in {'vlmcsd', 'vlmcs'} else 'noarch'
            for version in ['old-r1', config['PACKAGE_VERSION']]:
                path = packages / f'{name}-{version}.apk'
                path.touch()
                entries[path] = {'name': name, 'version': version, 'arch': arch}
        with patch.object(assemble, 'SDK', self.root), patch.object(assemble, 'metadata', side_effect=entries.__getitem__):
            selected = assemble.built_packages(config)
            self.assertEqual(set(selected), assemble.APP_NAMES)
            self.assertTrue(all(info['version'] == '9999-r7' for _, info in selected.values()))
            (packages / 'vlmcsd-9999-r7.apk').unlink()
            with self.assertRaisesRegex(RuntimeError, 'Missing built packages'):
                assemble.built_packages(config)


if __name__ == '__main__':
    unittest.main()
