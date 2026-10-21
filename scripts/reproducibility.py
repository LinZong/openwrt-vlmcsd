#!/usr/bin/env python3
"""Compare two builds of binaries and unsigned APKs in the same pinned SDK."""
import hashlib
import json
import subprocess
from pathlib import Path
from build_config import load_config, render
from build_helpers import build_jobs

root = Path(__file__).resolve().parent.parent
sdk = root / '.cache/sdk'
config = load_config()
jobs = build_jobs()


def hashes():
    build = sdk / f"build_dir/target-{config['OPENWRT_ARCH']}_{config['SDK_LIBC']}/vlmcsd-{config['UPSTREAM_COMMIT']}/bin"
    files = list(build.iterdir())
    for name in ['vlmcsd', 'vlmcs', 'luci-app-vlmcsd', 'luci-i18n-vlmcsd-zh-cn']:
        files.append(sdk / 'bin/packages' / config['OPENWRT_ARCH'] / 'base' / f"{name}-{config['PACKAGE_VERSION']}.apk")
    return {str(p.relative_to(sdk)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


before = hashes()
(sdk / 'vlmcsd-build.mk').write_text(render(config, 'make'))
for package in ['vlmcsd', 'luci-app-vlmcsd']:
    for target in ['clean', 'compile']:
        subprocess.check_call(['make', '-j' + str(jobs), '-r', '-C', 'package/' + package, 'TOPDIR=' + str(sdk), 'SOURCE_DATE_EPOCH=' + config['SOURCE_DATE_EPOCH'], target], cwd=sdk)
after = hashes()
result = {'ok': before == after, 'before': before, 'after': after}
(root / 'dist/reproducibility-results.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
if before != after:
    raise SystemExit('Reproducibility comparison failed')
print('Reproducible: binaries and all four unsigned APKs are byte-identical')
