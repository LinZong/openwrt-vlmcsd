#!/usr/bin/env python3
"""Runtime helpers for verified download reuse and build parallelism."""
import argparse
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from build_config import load_config, verify_sdk_checksums


def build_jobs():
    override = os.environ.get('BUILD_JOBS', '')
    if override:
        if not re.fullmatch(r'[1-9][0-9]*', override):
            raise ValueError('BUILD_JOBS must be a positive integer')
        return int(override)
    try:
        jobs = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        jobs = os.cpu_count() or 1
    # Docker may allow many CPUs in the affinity mask but impose a CPU quota.
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max':
            jobs = min(jobs, math.ceil(int(quota) / int(period)))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return max(1, jobs)


def verify_metadata(config, directory):
    key = directory / 'openwrt-release.asc'
    sums = directory / 'sha256sums'
    signature = directory / 'sha256sums.asc'
    if not all(path.is_file() and path.stat().st_size for path in [key, sums, signature]):
        return False
    try:
        verify_sdk_checksums(config, sums)
        with tempfile.TemporaryDirectory(prefix='gpg-', dir=directory) as home:
            keyring = str(Path(home) / 'release.gpg')
            subprocess.run(['gpg', '--batch', '--no-options', '--no-autostart', '--homedir', home,
                            '--dearmor', '--output', keyring, str(key)], check=True, capture_output=True)
            result = subprocess.run(['gpgv', '--homedir', home, '--keyring', keyring,
                                     '--status-fd', '1', str(signature), str(sums)],
                                    check=True, capture_output=True, text=True)
        # Pin the actual signer's primary fingerprint, including when a subkey signs.
        return any(len(fields) >= 3 and fields[:2] == ['[GNUPG:]', 'VALIDSIG']
                   and config['OPENWRT_KEY_FINGERPRINT'] in {fields[2], fields[-1]}
                   for fields in (line.split() for line in result.stdout.splitlines()))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False


def prepare_sdk_metadata(config, cache):
    """Reuse authenticated files; repair missing/stale data without trusting size alone."""
    cache.mkdir(parents=True, exist_ok=True)
    urls = {'openwrt-release.asc': config['OPENWRT_KEY_URL'],
            'sha256sums': config['SDK_URL'] + '/sha256sums',
            'sha256sums.asc': config['SDK_URL'] + '/sha256sums.asc'}
    if verify_metadata(config, cache):
        print('Reusing verified SDK checksums, signature and release key', flush=True)
        return
    # Never replace a cached file with a failed or partially verified download.
    with tempfile.TemporaryDirectory(prefix='sdk-metadata-', dir=cache) as temporary:
        stage = Path(temporary)
        downloaded = set()

        def fetch(name):
            print('Downloading ' + urls[name], flush=True)
            subprocess.run(['curl', '-fLsS', '--retry', '3', urls[name], '-o', str(stage / name)], check=True)
            downloaded.add(name)

        for name in urls:
            local = cache / name
            if local.is_file() and local.stat().st_size:
                shutil.copy2(local, stage / name)
            else:
                fetch(name)
        # First try to complete a partial cache without re-fetching valid companions.
        if not downloaded or not verify_metadata(config, stage):
            for name in urls:
                if name not in downloaded:
                    fetch(name)
            if not verify_metadata(config, stage):
                raise ValueError('Downloaded SDK metadata failed signature, fingerprint or checksum verification')
        for name in downloaded:
            (stage / name).replace(cache / name)
    print('SDK checksums, signature and release key verified', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['jobs', 'sdk-metadata'])
    args = parser.parse_args()
    try:
        if args.command == 'jobs':
            print(build_jobs())
        else:
            root = Path(__file__).resolve().parents[1]
            prepare_sdk_metadata(load_config(), root / '.cache')
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Build preparation failed: {exc}\n')


if __name__ == '__main__':
    main()
