#!/usr/bin/env python3
"""Compare two builds of binaries and unsigned native packages in the same pinned SDK."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from build_config import load_config, render
from build_helpers import build_jobs

root = Path(__file__).resolve().parent.parent
config = load_config(require_upstream=True)
sdk = root / config['SDK_PATH']
jobs = build_jobs()
# Match the SDK top-level environment, including fakeroot for direct package builds.
os.environ['PATH'] = str(sdk / 'staging_dir/host/bin') + os.pathsep + os.environ['PATH']
os.environ.update(STAGING_DIR_HOST=str(sdk / 'staging_dir/host'),
                  BUILD_JOBS=str(jobs), TZ='UTC', LC_ALL='C')


def hashes():
    build = sdk / f"build_dir/target-{config['OPENWRT_ARCH']}_{config['SDK_LIBC']}/vlmcsd-{config['UPSTREAM_COMMIT']}/bin"
    files = [build / name for name in ['vlmcsd', 'vlmcs', 'vlmcsd-timeout']]
    for name in ['vlmcsd', 'vlmcs', 'luci-app-vlmcsd', 'luci-i18n-vlmcsd-zh-cn']:
        arch = config['OPENWRT_ARCH'] if name in {'vlmcsd', 'vlmcs'} else 'all'
        filename = (f"{name}-{config['PACKAGE_VERSION']}.apk" if config['PACKAGE_FORMAT'] == 'apk'
                    else f"{name}_{config['PACKAGE_VERSION']}_{arch}.ipk")
        files.append(sdk / 'bin/packages' / config['OPENWRT_ARCH'] / 'base' / filename)
    return {str(p.relative_to(sdk)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


before = hashes()
(sdk / 'vlmcsd-build.mk').write_text(render(config, 'make'))
for package in ['vlmcsd', 'luci-app-vlmcsd']:
    for target in ['clean', 'compile']:
        subprocess.check_call(['make', '-j' + str(jobs), '-r', '-C', 'package/' + package, 'TOPDIR=' + str(sdk), 'SOURCE_DATE_EPOCH=' + config['SOURCE_DATE_EPOCH'], target], cwd=sdk)
after = hashes()
result = {'ok': before == after, 'before': before, 'after': after}
(root / ('dist/reproducibility-' + config['OPENWRT_VERSION'] + '.json')).write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
if before != after:
    raise SystemExit('Reproducibility comparison failed')
print('Reproducible: binaries and all four unsigned native packages are byte-identical')
