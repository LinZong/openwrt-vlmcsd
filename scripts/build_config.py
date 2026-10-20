#!/usr/bin/env python3
"""Load the single build lock and derive values for Python, shell and Make."""
import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / 'config/build.json'
STRING_KEYS = {
    'OPENWRT_VERSION', 'OPENWRT_TARGET', 'OPENWRT_ARCH', 'OPENWRT_DEVICE',
    'SDK_GCC_VERSION', 'SDK_LIBC', 'SDK_SHA256', 'OPENWRT_KEY_FINGERPRINT',
    'UPSTREAM_REPOSITORY', 'UPSTREAM_VERSION', 'UPSTREAM_COMMIT', 'UPSTREAM_SHA256',
    'CONTAINER_BASE_IMAGE', 'BUILDER_PLATFORM', 'VM_IMAGE_SHA256',
}
INTEGER_KEYS = {'SOURCE_DATE_EPOCH', 'PACKAGE_RELEASE'}


def load_config(path=CONFIG):
    config = json.loads(Path(path).read_text())
    if set(config) != STRING_KEYS | INTEGER_KEYS:
        raise ValueError('Build config has missing or unknown keys')
    for key in STRING_KEYS:
        value = config[key]
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_./:@+-]+', value):
            raise ValueError(f'Invalid build config value: {key}')
    for key in INTEGER_KEYS:
        if type(config[key]) is not int or config[key] < 0:
            raise ValueError(f'{key} must be a nonnegative integer')
    for key, size in [('SDK_SHA256', 64), ('UPSTREAM_SHA256', 64),
                      ('VM_IMAGE_SHA256', 64), ('UPSTREAM_COMMIT', 40),
                      ('OPENWRT_KEY_FINGERPRINT', 40)]:
        if not re.fullmatch(r'[a-fA-F0-9]{' + str(size) + '}', config[key]):
            raise ValueError(f'{key} must contain {size} hexadecimal characters')
    if not re.fullmatch(r'[^/]+/[^/]+', config['UPSTREAM_REPOSITORY']):
        raise ValueError('UPSTREAM_REPOSITORY must be owner/repository')
    if not re.fullmatch(r'[a-zA-Z0-9_]+/[a-zA-Z0-9_]+', config['OPENWRT_TARGET']):
        raise ValueError('OPENWRT_TARGET must be target/subtarget')
    if not re.fullmatch(r'[^@]+@sha256:[a-f0-9]{64}', config['CONTAINER_BASE_IMAGE']):
        raise ValueError('CONTAINER_BASE_IMAGE must pin a SHA-256 digest')
    c = {key: str(value) for key, value in config.items()}
    target = c['OPENWRT_TARGET'].replace('/', '-')
    c['SDK_URL'] = f"https://downloads.openwrt.org/releases/{c['OPENWRT_VERSION']}/targets/{c['OPENWRT_TARGET']}"
    c['SDK_FILE'] = f"openwrt-sdk-{c['OPENWRT_VERSION']}-{target}_gcc-{c['SDK_GCC_VERSION']}_{c['SDK_LIBC']}.Linux-x86_64.tar.zst"
    c['OPENWRT_KEY_URL'] = f"https://raw.githubusercontent.com/openwrt/keyring/master/gpg/0x{c['OPENWRT_KEY_FINGERPRINT'][-16:]}.asc"
    c['UPSTREAM_URL'] = f"https://github.com/{c['UPSTREAM_REPOSITORY']}"
    c['UPSTREAM_ARCHIVE_URL'] = f"https://codeload.github.com/{c['UPSTREAM_REPOSITORY']}/tar.gz/{c['UPSTREAM_COMMIT']}"
    c['PACKAGE_VERSION'] = f"{c['UPSTREAM_VERSION']}-r{c['PACKAGE_RELEASE']}"
    c['VLMCSD_BUILD_VERSION'] = f"{c['UPSTREAM_VERSION']}-openwrt-r{c['PACKAGE_RELEASE']}-{c['UPSTREAM_COMMIT'][:7]}"
    c['BUILDER_IMAGE'] = f"vlmcsd-builder:{c['OPENWRT_VERSION']}"
    c['VM_CONTAINER_IMAGE'] = f"vlmcsd-vm:{c['OPENWRT_VERSION']}"
    c['VM_FILE'] = f"openwrt-{c['OPENWRT_VERSION']}-{target}-{c['OPENWRT_DEVICE']}-ext4-combined.img.gz"
    c['VM_URL'] = f"{c['SDK_URL']}/{c['VM_FILE']}"
    target, subtarget = c['OPENWRT_TARGET'].split('/')
    c['TARGET_BOARD'] = target
    c['TARGET_SUBTARGET'] = f'{target}_{subtarget}'
    # Changes to the image or its offline setup must not reuse an old guest disk.
    vm_setup = Path(__file__).with_name('vm-prepare.sh').read_bytes()
    c['VM_CACHE_KEY'] = hashlib.sha256(c['VM_IMAGE_SHA256'].encode() + vm_setup).hexdigest()
    return c


def render(config, output):
    if output == 'shell':
        return '\n'.join(f'export {key}={shlex.quote(value)}' for key, value in sorted(config.items())) + '\n'
    if output == 'make':
        return '# Generated from config/build.json; do not edit.\n' + ''.join(
            f'{key} := {value}\n' for key, value in sorted(config.items()))
    return json.dumps(config, sort_keys=True, indent=2) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('format', choices=['shell', 'make', 'json'])
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--sdk-checksums', type=Path, help='Require the configured SDK in this signed checksum list')
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.sdk_checksums and not any(
                fields in ([config['SDK_SHA256'], config['SDK_FILE']],
                           [config['SDK_SHA256'], '*' + config['SDK_FILE']])
                for fields in (line.split() for line in args.sdk_checksums.read_text().splitlines())):
            raise ValueError('Configured SDK hash missing from signed checksum list')
        print(render(config, args.format), end='')
    except (ValueError, OSError) as exc:
        parser.exit(1, f'Invalid build config: {exc}\n')


if __name__ == '__main__':
    main()
