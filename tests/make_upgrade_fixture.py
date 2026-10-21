#!/usr/bin/env python3
"""Create APKs with the previous package revision to test exact downgrades."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

root = Path('/work')
apk = root / '.cache/sdk/staging_dir/host/bin/apk'
source = root / 'dist/release'
out = root / 'dist/test-old-release'
if out.exists():
    shutil.rmtree(out)
out.mkdir()
manifest = json.loads((source / 'release.json').read_text())
current_version = next(p['version'] for p in manifest['packages'] if p['name'] == 'vlmcsd')
version, revision = current_version.rsplit('-r', 1)
if int(revision) < 1:
    raise ValueError('Upgrade fixture requires a package revision of at least 1')
old_version = f'{version}-r{int(revision) - 1}'
packages = []
for package in manifest['packages']:
    work = root / '.cache' / ('fixture-' + package['name'])
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    payload = work / 'files'
    payload.mkdir()
    original = source / package['file']
    meta = json.loads(subprocess.check_output([str(apk), 'adbdump', '--format', 'json', str(original)]))
    subprocess.check_call([str(apk), '--allow-untrusted', 'extract', '--destination', str(payload), str(original)])
    for item in [payload, *payload.rglob('*')]:
        os.chown(item, 0, 0, follow_symlinks=False)
    version_file = payload / 'usr/share/vlmcsd/version'
    if version_file.exists():
        version_file.write_text(old_version + '\n')
    info = meta['info']
    dest = out / f"{package['name']}-{old_version}.apk"
    cmd = [str(apk), 'mkpkg']
    for field in ['name', 'arch', 'license', 'description', 'origin', 'url']:
        if field in info:
            cmd += ['--info', field + ':' + info[field]]
    cmd += ['--info', 'version:' + old_version, '--info', 'depends:' + ' '.join(info.get('depends', []))]
    for name, body in meta.get('scripts', {}).items():
        path = work / name
        path.write_text(body)
        cmd += ['--script', name + ':' + str(path)]
    cmd += ['--files', str(payload), '--output', str(dest)]
    subprocess.check_call(cmd)
    subprocess.check_call([str(apk), '--allow-untrusted', 'adbsign', '--reset-signatures', '--sign-key', str(root / '.secrets/vlmcsd.key'), str(dest)])
    subprocess.check_call([str(apk), '--keys-dir', str(source), 'verify', str(dest)])
    packages.append({**package, 'version': old_version, 'file': dest.name})
for name in ['recovery-timeout', 'vlmcsd.pem', 'LICENSE.vlmcsd', 'LICENSE.integration', 'build-config.json', 'upstream.json']:
    shutil.copy2(source / name, out / name)
manifest['packages'] = packages
manifest['files'] = [{'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size} for p in sorted(out.iterdir())]
(out / 'release.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
print('Test-only old release:', out)
