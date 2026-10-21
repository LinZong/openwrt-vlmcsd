#!/usr/bin/env python3
"""Integration tests only for the dedicated QEMU guest. Never accepts a target."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={ROOT}/.cache/vm_known_hosts', '-i', f'{ROOT}/.cache/vm_id', '-p', '22222', 'root@127.0.0.1']
COMMON = ['--target', 'root@127.0.0.1', '--port', '22222', '--identity', str(ROOT / '.cache/vm_id'), '--known-hosts', str(ROOT / '.cache/vm_known_hosts')]
RESULTS = []


def ssh(cmd):
    return subprocess.check_output(SSH + [cmd], text=True, timeout=30)


def cli(operation, command, *extra, good=True):
    r = subprocess.run([sys.executable, str(ROOT / 'scripts/manage.py'), operation, command, *COMMON, *extra], capture_output=True, text=True, timeout=280)
    print(r.stdout, end='', flush=True)
    if (r.returncode == 0) != good:
        raise AssertionError(r.stdout + r.stderr)
    return r


def plan_apply(operation='deploy', good=True, *extra):
    plan = ROOT / 'dist' / 'test-plan.json'
    cli(operation, 'plan', '--output', str(plan), *extra)
    started = time.monotonic()
    r = cli(operation, 'apply', '--plan', str(plan), good=good)
    elapsed = round(time.monotonic() - started, 2)
    txid = next(line.split(': ', 1)[1] for line in r.stdout.splitlines() if line.startswith('Transaction:'))
    return txid, elapsed


def check(name, fn):
    started = time.monotonic()
    result = fn()
    RESULTS.append({'test': name, 'ok': True, 'seconds': round(time.monotonic() - started, 2), 'details': result})
    print('PASS:', name, flush=True)


def status():
    return json.loads(ssh('ubus call luci.vlmcsd status'))


def main():
    board = json.loads(ssh('ubus call system board'))
    assert 'QEMU' in board['model'], 'Refusing to test a non-QEMU target'
    release = json.loads((ROOT / 'dist/release/release.json').read_text())
    assert board['release']['version'] == release['openwrt']
    check('deploy', lambda: plan_apply())
    def service():
        s = status()
        assert s['running'] and len(s['listeners']) == 2, s
        uid = ssh(f"grep '^Uid:' /proc/{s['pid']}/status").split()[1]
        assert uid != '0', 'Daemon must not run as root'
        for protocol in [4, 5, 6]:
            r = json.loads(ssh(f'ubus call luci.vlmcsd selftest \'{{"protocol":{protocol}}}\''))
            assert r['ok'] and 'Response from KMS server' in r['output'], r
        return s
    check('non-root service and all protocols', service)
    def rpc_reject():
        r = json.loads(ssh('ubus call luci.vlmcsd action \'{"action":"start;reboot"}\''))
        assert not r['ok']
        r = json.loads(ssh('ubus call luci.vlmcsd selftest \'{"protocol":3}\''))
        assert not r['ok']
        session = json.loads(ssh('ubus call session create'))['ubus_rpc_session']
        r = json.loads(ssh(f'ubus call session access \'{{"ubus_rpc_session":"{session}","scope":"ubus","object":"luci.vlmcsd","function":"action"}}\''))
        assert not r['access']
    check('RPC argument and anonymous ACL rejection', rpc_reject)
    def invalid_config():
        ssh('uci set vlmcsd.main.max_workers=999; uci commit vlmcsd')
        r = subprocess.run(SSH + ['/etc/init.d/vlmcsd reload'], capture_output=True)
        assert r.returncode != 0
        assert status()['running']
        ssh('uci set vlmcsd.main.max_workers=4; uci commit vlmcsd')
    check('backend validation preserves running service', invalid_config)
    def stale_plan():
        p = str(ROOT / 'dist' / 'stale-plan.json')
        cli('deploy', 'plan', '--output', p)
        ssh('uci set vlmcsd.main.verbose=1; uci commit vlmcsd')
        cli('deploy', 'apply', '--plan', p, good=False)
        ssh('uci set vlmcsd.main.verbose=0; uci commit vlmcsd')
    check('changed target rejects reviewed plan', stale_plan)
    def dns():
        ssh('uci set vlmcsd.main.dns=1; uci commit vlmcsd; /etc/init.d/vlmcsd reload')
        record = ssh('uci show dhcp.vlmcsd_srv')
        assert '_vlmcs._tcp.lan' in record
        deadline = time.monotonic() + 10
        answer = ''
        while time.monotonic() < deadline:
            try:
                answer = ssh('nslookup -type=SRV _vlmcs._tcp.lan 127.0.0.1')
                if '1688' in answer:
                    break
            except subprocess.CalledProcessError:
                pass
            time.sleep(.5)
        assert '1688' in answer, answer
        ssh('uci set vlmcsd.main.dns=0; uci commit vlmcsd; /etc/init.d/vlmcsd reload')
        assert not ssh('uci -q show dhcp.vlmcsd_srv || true').strip()
        return answer
    check('DNS publish and revoke', dns)
    check('uninstall preserves config', lambda: plan_apply('uninstall'))
    assert 'enabled' in ssh('cat /etc/config/vlmcsd')
    def automatic_rollback():
        ssh("nft add table inet vlmcsd_test; nft 'add chain inet vlmcsd_test output { type filter hook output priority -100; policy accept; }'; nft add rule inet vlmcsd_test output ip daddr 127.0.0.1 tcp dport 1688 drop")
        try:
            tx, elapsed = plan_apply('deploy', False)
            state = ssh(f'cat /root/vlmcsd-deploy/{tx}/state').strip()
            assert state == 'rolled_back', state
            assert elapsed < 60, elapsed
            assert not ssh('test -x /usr/sbin/vlmcsd && echo installed || true').strip()
            return {'transaction': tx, 'seconds': elapsed}
        finally:
            ssh('nft delete table inet vlmcsd_test')
    check('protocol failure automatically rolls back', automatic_rollback)
    def manual_rollback():
        tx, elapsed = plan_apply()
        cli('deploy', 'rollback', '--transaction', tx)
        assert ssh(f'cat /root/vlmcsd-deploy/{tx}/state').strip() == 'rolled_back'
        assert not ssh('test -x /usr/sbin/vlmcsd && echo installed || true').strip()
        return {'transaction': tx, 'seconds': elapsed}
    check('manual rollback after successful deployment', manual_rollback)
    check('final deployment for UI inspection', lambda: plan_apply())
    return board


if __name__ == '__main__':
    report = ROOT / 'dist' / 'integration-results.json'
    try:
        board = main()
        report.write_text(json.dumps({'board': board, 'results': RESULTS, 'ok': True}, indent=2) + '\n')
    except Exception as e:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({'results': RESULTS, 'ok': False, 'error': str(e)}, indent=2) + '\n')
        raise
