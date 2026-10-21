#!/usr/bin/env python3
"""Assemble native APK/IPK packages and produce an authenticated release."""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from build_config import load_config, STRING_KEYS, INTEGER_KEYS, OPTIONAL_KEYS
import ipk

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / '.cache/sdk'
APK = SDK / 'staging_dir/host/bin/apk'
OUT = ROOT / '.cache/release-stage'
SECRETS = ROOT / '.secrets'
APP_NAMES = {'vlmcsd', 'vlmcs', 'luci-app-vlmcsd', 'luci-i18n-vlmcsd-zh-cn'}


def run(*args):
    return subprocess.check_output([str(x) for x in args], text=True)


def metadata(path):
    d = json.loads(run(APK, 'adbdump', '--format', 'json', path))
    # adbdump uses a top-level metadata object for APK v3 package schema.
    if isinstance(d, list):
        d = d[0]
    return d.get('info', d)


def built_packages(config):
    candidates = {}
    fmt = config['PACKAGE_FORMAT']
    for path in SDK.glob('bin/packages/**/*.' + fmt):
        info = metadata(path) if fmt == 'apk' else ipk.metadata(path)
        name = info['name']
        arch = config['OPENWRT_ARCH'] if name in {'vlmcsd', 'vlmcs'} else ('noarch' if fmt == 'apk' else 'all')
        if name in APP_NAMES and info['version'] == config['PACKAGE_VERSION'] and info['arch'] == arch:
            if fmt == 'ipk':
                for kind in ['control', 'data']:
                    with ipk.archive(path, kind) as archive:
                        if any(m.uid != 0 or m.gid != 0 for m in archive):
                            raise RuntimeError('IPK contains host ownership; check SDK fakeroot: ' + str(path))
            candidates[name] = (path, info)
    missing = APP_NAMES - candidates.keys()
    if missing:
        raise RuntimeError('Missing built packages for ' + config['PACKAGE_VERSION'] + ': ' + ', '.join(sorted(missing)))
    return candidates


def main():
    global SDK, APK, OUT
    config = load_config(require_upstream=True)
    SDK = ROOT / config['SDK_PATH']
    APK = SDK / 'staging_dir/host/bin/apk'
    OUT = ROOT / (config['SDK_METADATA_PATH'] + '/release-stage')
    candidates = built_packages(config)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(mode=0o700, exist_ok=True)
    key = SECRETS / 'vlmcsd.key'
    if not key.exists():
        subprocess.check_call(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', str(key)], stderr=subprocess.DEVNULL)
        key.chmod(0o600)
    subprocess.check_call(['openssl', 'pkey', '-in', str(key), '-pubout', '-out', str(OUT / 'vlmcsd.pem')])
    packages = []
    for name in sorted(APP_NAMES):
        f, info = candidates[name]
        dest = OUT / f.name
        shutil.copy2(f, dest)
        if config['PACKAGE_FORMAT'] == 'apk':
            subprocess.check_call([str(APK), '--allow-untrusted', 'adbsign', '--sign-key', str(key), '--reset-signatures', str(dest)])
            subprocess.check_call([str(APK), '--keys-dir', str(OUT), 'verify', str(dest)])
        packages.append({'name': name, 'version': info['version'], 'arch': info['arch'], 'depends': info.get('depends', []), 'file': f.name})
    if config['PACKAGE_FORMAT'] == 'apk':
        extraction = OUT / 'timeout-extract'
        extraction.mkdir()
        subprocess.check_call([str(APK), '--allow-untrusted', 'extract', '--destination', str(extraction), str(candidates['vlmcsd'][0])])
        shutil.copy2(extraction / 'usr/libexec/vlmcsd-timeout', OUT / 'recovery-timeout')
        shutil.rmtree(extraction)
    else:
        (OUT / 'recovery-timeout').write_bytes(ipk.payload_file(candidates['vlmcsd'][0], 'usr/libexec/vlmcsd-timeout'))
    (OUT / 'recovery-timeout').chmod(0o700)
    with tarfile.open(ROOT / config['UPSTREAM_ARCHIVE_PATH']) as source_archive:
        member = next(m for m in source_archive.getmembers() if m.name.endswith('/LICENSE') and m.name.count('/') == 1)
        (OUT / 'LICENSE.vlmcsd').write_bytes(source_archive.extractfile(member).read())
    shutil.copy2(ROOT / 'LICENSE', OUT / 'LICENSE.integration')
    snapshot = {key: int(config[key]) if key in INTEGER_KEYS else config[key]
                for key in STRING_KEYS | INTEGER_KEYS | (OPTIONAL_KEYS & config.keys())}
    snapshot.update(UPSTREAM_VERSION=config['UPSTREAM_VERSION'], UPSTREAM_SHA256=config['UPSTREAM_SHA256'],
                    SOURCE_DATE_EPOCH=int(config['SOURCE_DATE_EPOCH']))
    (OUT / 'build-config.json').write_text(json.dumps(snapshot, indent=2, sort_keys=True) + '\n')
    shutil.copy2(ROOT / '.cache/upstream.json', OUT / 'upstream.json')
    if (ROOT / config['SDK_METADATA_PATH'] / 'builder-image.txt').exists():
        shutil.copy2(ROOT / config['SDK_METADATA_PATH'] / 'builder-image.txt', OUT / 'builder-image.txt')
    files = [{'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size}
             for p in sorted(OUT.iterdir()) if p.is_file() and p.name not in {'release.json', 'SHA256SUMS'}]
    manifest = {'schema': 1, 'package_format': config['PACKAGE_FORMAT'], 'package_manager': config['PACKAGE_MANAGER'], 'openwrt': config['OPENWRT_VERSION'], 'arch': config['OPENWRT_ARCH'], 'target': config['OPENWRT_TARGET'],
                'elf_machine': config['ELF_MACHINE'], 'elf_interpreter': config['ELF_INTERPRETER'], 'source': config['UPSTREAM_URL'],
                'commit': config['UPSTREAM_COMMIT'], 'source_date_epoch': int(config['SOURCE_DATE_EPOCH']),
                'sdk_sha256': config['SDK_SHA256'], 'upstream_sha256': config['UPSTREAM_SHA256'],
                'upstream_version': config['UPSTREAM_VERSION'], 'upstream_tree': config['UPSTREAM_TREE'],
                'container_base_image': config['CONTAINER_BASE_IMAGE'],
                'feeds': (SDK / 'feeds.conf.default').read_text(), 'packages': packages, 'files': files}
    (OUT / 'release.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    (OUT / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(OUT.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
    if config['PACKAGE_FORMAT'] == 'ipk':
        # Native opkg accepts the SDK-generated IPKs unchanged. Authenticate the bundle separately.
        subprocess.check_call(['openssl', 'dgst', '-sha256', '-sign', str(key), '-out', str(OUT / 'SHA256SUMS.sig'), str(OUT / 'SHA256SUMS')])
        subprocess.check_call(['openssl', 'dgst', '-sha256', '-verify', str(OUT / 'vlmcsd.pem'), '-signature', str(OUT / 'SHA256SUMS.sig'), str(OUT / 'SHA256SUMS')])
    destination = ROOT / config['RELEASE_PATH']
    previous = destination.with_name(destination.name + '.previous')
    if previous.exists():
        shutil.rmtree(previous)
    if destination.exists():
        destination.rename(previous)
    OUT.rename(destination)
    print('Assembled', len(packages), config['PACKAGE_FORMAT'].upper(), 'packages in', destination)


if __name__ == '__main__':
    main()
