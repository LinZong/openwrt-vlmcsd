#!/usr/bin/env python3
"""Fetch an exact Git commit and generate a cached, deterministic source archive."""
import gzip
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from build_config import load_config, upstream_version


def git(repository, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
                                    '--git-dir', str(repository), *args], text=True, stderr=subprocess.PIPE).strip()


def prepare_upstream(config, cache):
    cache = Path(cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(config['UPSTREAM_REPOSITORY'].encode()).hexdigest()
    repository = cache / 'upstream-git' / (identity + '.git')
    if not repository.exists():
        repository.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(['git', 'init', '--quiet', '--bare', str(repository)])
    commit = config['UPSTREAM_COMMIT']
    try:
        observed = git(repository, 'rev-parse', '--verify', commit + '^{commit}')
    except subprocess.CalledProcessError:
        print('Fetching upstream commit ' + commit, flush=True)
        git(repository, 'fetch', '--quiet', '--no-tags', '--no-recurse-submodules',
            '--depth', '1', config['UPSTREAM_GIT_URL'], commit)
        observed = git(repository, 'rev-parse', '--verify', commit + '^{commit}')
    if observed != commit:
        raise ValueError('Fetched Git commit differs from UPSTREAM_COMMIT')
    git(repository, 'fsck', '--strict', '--no-reflogs', '--no-dangling', commit)
    # Match git archive / GitHub source archive semantics: record gitlinks without
    # fetching their external contents (the current vlmcsd gitlink is its wiki).
    submodules = {}
    for line in git(repository, 'ls-tree', '-r', commit).splitlines():
        if line.startswith('160000 '):
            entry, path = line.split('\t', 1)
            submodules[path] = entry.split()[2]
    epoch = int(git(repository, 'show', '-s', '--format=%ct', commit))
    if epoch < 0:
        raise ValueError('Upstream commit timestamp must be nonnegative')
    lock = {'schema': 1, 'repository': config['UPSTREAM_REPOSITORY'], 'commit': commit,
            'tree': git(repository, 'rev-parse', commit + '^{tree}'),
            'source_date_epoch': epoch, 'version': upstream_version(commit, epoch), 'submodules': submodules}
    sources = cache / 'upstream-sources'
    sources.mkdir(exist_ok=True)
    archive = sources / config['UPSTREAM_SOURCE_FILE']
    lock_path = cache / 'upstream.json'
    try:
        existing = json.loads(lock_path.read_text())
        if (all(existing.get(key) == value for key, value in lock.items())
                and archive.is_file()
                and hashlib.sha256(archive.read_bytes()).hexdigest() == existing.get('archive_sha256')):
            print('Reusing verified Git source: ' + config['UPSTREAM_SOURCE_FILE'], flush=True)
            return existing
    except (OSError, ValueError, AttributeError):
        pass
    with tempfile.TemporaryDirectory(prefix='upstream-', dir=cache) as temporary:
        temporary = Path(temporary)
        tar_path = temporary / 'source.tar'
        with tar_path.open('wb') as output:
            subprocess.run(['git', '-c', 'tar.umask=0022', '-c', 'core.attributesFile=/dev/null',
                            '--git-dir', str(repository), 'archive', '--format=tar',
                            '--prefix=vlmcsd-' + commit + '/', commit], stdout=output, check=True)
        compressed = temporary / 'source.tar.gz'
        with tar_path.open('rb') as source, compressed.open('wb') as output:
            with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0, compresslevel=9) as archive_file:
                shutil.copyfileobj(source, archive_file)
        lock['archive_sha256'] = hashlib.sha256(compressed.read_bytes()).hexdigest()
        metadata = temporary / 'upstream.json'
        metadata.write_text(json.dumps(lock, sort_keys=True, indent=2) + '\n')
        compressed.replace(archive)
        metadata.replace(lock_path)
    print('Generated upstream version ' + lock['version'] + ' and source archive', flush=True)
    return lock


def main():
    try:
        prepare_upstream(load_config(), Path(__file__).resolve().parents[1] / '.cache')
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        raise SystemExit(f'Upstream preparation failed: {detail}') from None


if __name__ == '__main__':
    main()
