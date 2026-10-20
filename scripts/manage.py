#!/usr/bin/env python3
"""Read-only plan and explicitly invoked SSH transaction driver (stdlib only)."""
import argparse
import hashlib
import io
import json
import re
import shlex
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
APP_NAMES = {'vlmcsd', 'vlmcs', 'luci-app-vlmcsd', 'luci-i18n-vlmcsd-zh-cn'}
REMOTE = '/root/vlmcsd-deploy'
PAYLOAD_SCRIPTS = ['engine.sh', 'snapshot.sh', 'check-removals.sh']
REVIEW_SCRIPTS = PAYLOAD_SCRIPTS + ['manage.py', 'deploy.sh', 'uninstall.sh']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def ssh(args, command, data=None, timeout=30):
    if not re.fullmatch(r'[A-Za-z0-9_.@:-]+', args.target) or args.target.startswith('-'):
        raise ValueError('Invalid SSH target')
    cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-p', str(args.port)]
    if args.identity:
        cmd += ['-i', str(Path(args.identity).resolve())]
    if args.known_hosts:
        cmd += ['-o', 'UserKnownHostsFile=' + str(Path(args.known_hosts).resolve())]
    cmd += [args.target, command]
    result = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ConnectionError(f'SSH command failed ({result.returncode}): {result.stderr.decode(errors="replace")} {result.stdout.decode(errors="replace")}')
    return result.stdout


def snapshot(args):
    s = json.loads(ssh(args, '/bin/sh -s', (SCRIPTS / 'snapshot.sh').read_bytes()))
    s['installed'] = {p['name']: p for p in json.loads(s['packages'])}
    return s


def fingerprint(s):
    return digest(json_bytes({k: s[k] for k in ['board', 'hashes', 'world', 'enabled', 'installed', 'pending', 'ownership']}))


def scripts_hash():
    return {name: digest((SCRIPTS / name).read_bytes()) for name in REVIEW_SCRIPTS}


def load_release(path):
    path = Path(path).resolve()
    m = json.loads((path / 'release.json').read_text())
    for artifact in m['files']:
        p = path / artifact['file']
        if p.parent != path or not p.is_file() or digest(p.read_bytes()) != artifact['sha256']:
            raise ValueError('Release artifact missing or modified: ' + artifact['file'])
    return path, m


def baseline_check(s, manifest):
    b = s['board']
    if b['DISTRIB_RELEASE'] != manifest['openwrt'] or b['DISTRIB_ARCH'] != manifest['arch']:
        raise ValueError(f"This release requires OpenWrt {manifest['openwrt']} {manifest['arch']}")
    if s['pending'].strip():
        raise ValueError('Commit or revert pending UCI changes before planning')
    if int(s['available_kib']) < 16384:
        raise ValueError('At least 16 MiB persistent free space is required')
    observed = json.loads(s.get('observed_status', '{}'))
    if ':1688' in s['listening'] and not any(str(endpoint).endswith(':1688') for endpoint in observed.get('listeners', [])):
        raise ValueError('TCP 1688 is occupied by another process')


def old_artifacts(names, s, release, manifest, cache):
    """Never claim rollback is available without exact prior package bytes."""
    result = []
    candidates = list(release.glob('*.apk')) + list(Path(cache).resolve().glob('*.apk'))
    for name in names:
        if name not in s['installed']:
            continue
        version = s['installed'][name]['version']
        filename = f'{name}-{version}.apk'
        matches = [p for p in candidates if p.name == filename]
        if not matches:
            raise ValueError(f'Missing exact rollback package {filename}; place it in {cache}')
        p = matches[0]
        result.append({'file': str(p), 'name': name, 'version': version, 'sha256': digest(p.read_bytes())})
    return result


def make_plan(args):
    release, manifest = load_release(args.release)
    s = snapshot(args)
    baseline_check(s, manifest)
    available = {p['name']: p for p in manifest['packages']}
    selected = set(APP_NAMES & available.keys())
    if not {'vlmcsd', 'vlmcs', 'luci-app-vlmcsd'} <= selected:
        raise ValueError('Incomplete application release')
    if args.operation == 'deploy':
        todo = list(selected)
        while todo:
            package = available[todo.pop()]
            for dep in package.get('depends', []):
                name = re.split(r'[<>=~]', dep, maxsplit=1)[0]
                if name.startswith('!') or name in s['installed'] or name in selected:
                    continue
                if name not in available:
                    raise ValueError('Missing offline dependency: ' + dep)
                selected.add(name)
                todo.append(name)
        install = [available[n] for n in sorted(selected)]
        remove = []
        # SDK dependency packages already installed are never reinstalled/upgraded.
        changed = selected
    else:
        install = []
        remove = sorted(APP_NAMES & s['installed'].keys())
        ownership = json.loads(s.get('ownership', '{}'))
        introduced = set(ownership.get('introduced', [])) & s['installed'].keys()
        for name in sorted(introduced - APP_NAMES):
            dependents = [n for n, package in s['installed'].items() if n not in APP_NAMES | introduced
                          and any(re.split(r'[<>=~]', d, maxsplit=1)[0] == name for d in package.get('depends', []))]
            if not dependents:
                remove.append(name)
        if not remove:
            raise ValueError('Application is not installed')
        changed = set(remove)
    old = old_artifacts(changed, s, release, manifest, args.rollback_dir)
    settings = s.get('settings', {})
    dns = int(args.dns) if args.dns is not None else int(settings.get('dns') or 0)
    desired = {'enabled': '1', 'interface': settings.get('interface') or 'lan', 'port': settings.get('port') or '1688', 'dns': str(dns)}
    plan = {
        'schema': 1, 'operation': args.operation, 'target': args.target, 'port': args.port,
        'release': str(release), 'release_sha256': digest((release / 'release.json').read_bytes()),
        'baseline': s, 'baseline_fingerprint': fingerprint(s),
        'scripts': scripts_hash(), 'install': install, 'remove': remove,
        'rollback_packages': old,
        'new_names': sorted(changed - s['installed'].keys()),
        'dns': dns, 'configuration': desired, 'purge': bool(args.purge),
        'timeouts': {'deployment': 120, 'health': 10, 'rollback': 120, 'confirmation': 0},
        'changes': [
            'Local APK transaction only; no unrelated upgrades',
            'Manage vlmcsd UCI, process, rc.d, dedicated account, LuCI/RPC files',
            'DNS SRV managed section enabled' if dns else 'DNS discovery disabled',
            'Persistent recovery journal and temporary boot recovery service',
        ],
    }
    output = Path(args.output or ROOT / 'dist' / 'plans' / f'{args.operation}-{args.target.replace("@", "_")}.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json_bytes(plan))
    report = output.with_suffix('.md')
    report.write_text('# Deployment review\n\n' + '\n'.join([
        f'- Operation: `{plan["operation"]}`', f'- Target: `{args.target}:{args.port}`',
        f'- Plan SHA256: `{digest(output.read_bytes())}`',
        '- Execution / health / recovery limits: 120 / 10 / 120 seconds; no confirmation wait.',
        '- Install: ' + ', '.join(p['name'] + '=' + p['version'] for p in install),
        '- Remove: ' + ', '.join(remove), '- New packages: ' + ', '.join(plan['new_names']),
        '- Desired UCI settings: `' + json.dumps(desired, sort_keys=True) + '`; preserve other options.',
        '- Exact previous packages staged: ' + ', '.join(p['name'] + '=' + p['version'] for p in old),
        *['- ' + c for c in plan['changes']],
        '\nReview this plan before invoking apply. No target files have been written.\n',
    ]))
    print(f'Plan: {output}\nReview: {report}\nSHA256: {digest(output.read_bytes())}')


def tar_add(archive, name, data, mode=0o600):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    archive.addfile(info, io.BytesIO(data))


def apply(args):
    if not args.plan:
        raise ValueError('--plan is required')
    plan_path = Path(args.plan).resolve()
    p = json.loads(plan_path.read_text())
    if p['operation'] != args.operation or p['target'] != args.target or p['port'] != args.port:
        raise ValueError('Operation/target differs from the reviewed plan')
    if scripts_hash() != p['scripts']:
        raise ValueError('Deployment scripts changed; regenerate the plan')
    release, manifest = load_release(p['release'])
    if digest((release / 'release.json').read_bytes()) != p['release_sha256']:
        raise ValueError('Release changed; regenerate the plan')
    s = snapshot(args)
    baseline_check(s, manifest)
    if fingerprint(s) != p['baseline_fingerprint']:
        raise ValueError('Target changed; regenerate and review the plan')
    txid = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    remote = REMOTE + '/' + txid
    audit = ROOT / 'dist' / 'transactions' / txid
    audit.mkdir(parents=True, mode=0o700)
    (audit / 'plan.json').write_bytes(plan_path.read_bytes())
    ownership = json.loads(s.get('ownership', '{}'))
    request = {'operation': args.operation, 'dns': p['dns'], 'purge': p['purge'],
               'interface': p['configuration']['interface'], 'service_port': p['configuration']['port'],
               'ownership': {'introduced': sorted(set(ownership.get('introduced', [])) | set(p['new_names'])),
                             'account_created': ownership.get('account_created', not bool(s.get('account'))),
                             'group_created': ownership.get('group_created', not bool(s.get('group')))}}
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as tar:
        for name in PAYLOAD_SCRIPTS:
            tar_add(tar, name, (SCRIPTS / name).read_bytes(), 0o700)
        tar_add(tar, 'request.json', json_bytes(request))
        tar_add(tar, 'empty-repositories', b'')
        tar_add(tar, 'state', b'staged\n')
        tar_add(tar, 'new-names', ('\n'.join(p['new_names']) + '\n' if p['new_names'] else '').encode())
        tar_add(tar, 'remove-names', ('\n'.join(p['remove']) + '\n').encode())
        tar_add(tar, 'allowed-names', ('\n'.join(sorted({x['name'] for x in p['install']} | set(p['remove']))) + '\n').encode())
        tar_add(tar, 'old-installed', ('\n'.join(x['name'] for x in p['rollback_packages']) + '\n' if p['rollback_packages'] else '').encode())
        tar_add(tar, 'timeout', (release / 'recovery-timeout').read_bytes(), 0o700)
        tar_add(tar, 'keys/vlmcsd.pem', (release / 'vlmcsd.pem').read_bytes())
        for package in p['install']:
            f = release / package['file']
            tar_add(tar, 'packages/' + f.name, f.read_bytes())
        for package in p['rollback_packages']:
            f = Path(package['file'])
            if digest(f.read_bytes()) != package['sha256']:
                raise ValueError('Rollback package modified')
            tar_add(tar, 'rollback-packages/' + f.name, f.read_bytes())
        expected = ''.join(f'{h}  {f}\n' for f, h in s['hashes'].items() if h != 'absent')
        tar_add(tar, 'expected-hashes', expected.encode())
        tar_add(tar, 'expected-absent', '\n'.join(f for f,h in s['hashes'].items() if h == 'absent').encode())
        tar_add(tar, 'expected-packages', s['packages'].strip().encode())
    ssh(args, f'umask 077; mkdir -p {shlex.quote(remote)}; tar -xzf - -C {shlex.quote(remote)}', data.getvalue(), timeout=60)
    # Detached execution survives SSH failure. Outcome is always retrieved from its journal.
    command = f'cp /etc/apk/keys/*.pem {shlex.quote(remote)}/keys/; setsid /bin/sh {shlex.quote(remote)}/engine.sh apply </dev/null >{shlex.quote(remote)}/launcher.log 2>&1 &'
    ssh(args, command)
    print(f'Transaction: {txid}\nTarget journal: {remote}\nLocal report: {audit}', flush=True)
    deadline = time.monotonic() + 270
    previous = None
    while time.monotonic() < deadline:
        try:
            state = ssh(args, f'cat {shlex.quote(remote)}/state', timeout=15).decode().strip()
            if state != previous:
                print('State:', state, flush=True)
                previous = state
            if state in {'succeeded', 'rolled_back', 'recovery_failed', 'rejected'}:
                collect(args, txid, audit)
                if state != 'succeeded':
                    raise RuntimeError(f'Transaction ended as {state}; see {audit}')
                return
        except (subprocess.TimeoutExpired, OSError, ConnectionError) as exc:
            print(f'Connection interrupted; router recovery remains active: {exc}', file=sys.stderr)
        time.sleep(1)
    raise RuntimeError(f'Outcome not confirmed. Run status --transaction {txid}; do not repeat apply.')


def valid_txid(txid):
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}', txid):
        raise ValueError('Invalid transaction identifier')
    return REMOTE + '/' + txid


def collect(args, txid, audit):
    remote = valid_txid(txid)
    # Store the raw tar, avoiding unsafe extraction of any target-supplied paths.
    result = ssh(args, f'tar -czf - -C {shlex.quote(remote)} state events.jsonl launcher.log worker.log before request.json after-snapshot.json rollback-snapshot.json status.json protocol-4.log protocol-5.log protocol-6.log 2>/dev/null || true')
    (audit / 'target-report.tar.gz').write_bytes(result)
    events = ssh(args, f'cat {shlex.quote(remote)}/events.jsonl 2>/dev/null || true')
    (audit / 'events.jsonl').write_bytes(events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['deploy', 'uninstall'])
    parser.add_argument('command', choices=['plan', 'apply', 'status', 'rollback'])
    parser.add_argument('--target', default='gateway')
    parser.add_argument('--port', default=22, type=int)
    parser.add_argument('--identity')
    parser.add_argument('--known-hosts')
    parser.add_argument('--release', default=str(ROOT / 'dist' / 'release'))
    parser.add_argument('--rollback-dir', default=str(ROOT / 'dist' / 'rollback-package-cache'))
    parser.add_argument('--output')
    parser.add_argument('--plan')
    parser.add_argument('--transaction')
    dns_options = parser.add_mutually_exclusive_group()
    dns_options.add_argument('--dns', dest='dns', action='store_true', default=None)
    dns_options.add_argument('--no-dns', dest='dns', action='store_false')
    parser.add_argument('--purge', action='store_true')
    args = parser.parse_args()
    if args.command == 'plan':
        make_plan(args)
    elif args.command == 'apply':
        apply(args)
    elif args.command == 'status':
        if args.transaction:
            remote = valid_txid(args.transaction)
            print(ssh(args, f'cat {shlex.quote(remote)}/state; tail -15 {shlex.quote(remote)}/events.jsonl').decode())
        else:
            print(json.dumps(snapshot(args), ensure_ascii=False, indent=2))
    else:
        remote = valid_txid(args.transaction or '')
        # Rollback is also explicit; report remains on the target if the link drops.
        print(ssh(args, f'/bin/sh {shlex.quote(remote)}/engine.sh rollback', timeout=135).decode())


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print('ERROR:', exc, file=sys.stderr)
        sys.exit(1)
