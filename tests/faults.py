#!/usr/bin/env python3
"""Destructive fault injection, restricted to the dedicated loopback QEMU guest."""
import json
import subprocess
import sys
import time
from pathlib import Path
import integration as test

ROOT = test.ROOT
REPORT = []


def until(fn, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(0.5)
    raise AssertionError('Condition did not become true within deadline')


def fault(mode):
    board = json.loads(test.ssh('ubus call system board'))
    assert ('QEMU' in board['model'] or (board['model'] == 'linux,dummy-virt' and board['release']['target'] == 'armsr/armv8'))
    assert board['release']['version'] == test.CONFIG['OPENWRT_VERSION']
    assert board['release']['target'] == test.CONFIG['OPENWRT_TARGET']
    installed = test.ssh('apk query --from installed --format json --fields name,version "*"')
    if any(p['name'] == 'vlmcsd' for p in json.loads(installed)):
        test.plan_apply('uninstall')
    baseline = json.loads(test.ssh('apk query --from installed --format json --fields name,version "*"'))
    test.ssh("nft add table inet vlmcsd_test; nft 'add chain inet vlmcsd_test output { type filter hook output priority -100; policy accept; }'; nft add rule inet vlmcsd_test output ip daddr 127.0.0.1 tcp dport 1688 drop")
    plan = ROOT / 'dist' / f"fault-{test.CONFIG['REPORT_SUFFIX']}-{mode}.json"
    test.cli('deploy', 'plan', '--output', str(plan))
    logpath = test.VM / f'fault-{mode}.log'
    started = time.monotonic()
    with logpath.open('w') as output:
        proc = subprocess.Popen([sys.executable, str(ROOT / 'scripts/manage.py'), 'deploy', 'apply', *test.COMMON, '--plan', str(plan)], stdout=output, stderr=subprocess.STDOUT)
        try:
            def transaction():
                for line in logpath.read_text().splitlines():
                    if line.startswith('Transaction: '):
                        return line.split(': ', 1)[1]
            tx = until(transaction)
            remote = '/root/vlmcsd-deploy/' + tx
            until(lambda: test.ssh(f'test -e {remote}/protocol-4.log && echo ready').strip())
            if mode == 'executor-killed':
                test.ssh(f'kill -KILL $(cat {remote}/executor-pid)')
            elif mode == 'deadline':
                # Age the journal to exercise the independent watcher without a 120s idle test.
                test.ssh(f'kill -STOP $(cat {remote}/executor-pid); now=$(cut -d. -f1 /proc/uptime); echo $((now - 126)) > {remote}/start-uptime')
            elif mode == 'reboot':
                subprocess.run(test.SSH + ['reboot'], capture_output=True, timeout=10)
                time.sleep(4)
                def guest_ready():
                    result = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Running}}', 'vlmcsd-test-vm'], text=True).strip()
                    if result != 'true':
                        subprocess.check_call(['docker', 'start', 'vlmcsd-test-vm'], stdout=subprocess.DEVNULL)
                        return False
                    return test.ssh('echo ready').strip() == 'ready'
                until(guest_ready, seconds=80)
            else:
                raise AssertionError(mode)
            proc.wait(timeout=150)
            state = test.ssh(f'cat {remote}/state').strip()
            assert state == 'rolled_back', (state, logpath.read_text())
            actual = json.loads(test.ssh('apk query --from installed --format json --fields name,version "*"'))
            assert actual == baseline, 'Package versions did not return to the exact baseline'
            assert not test.ssh('test -x /usr/sbin/vlmcsd && echo installed || true').strip()
            events = test.ssh(f'cat {remote}/events.jsonl')
            (ROOT / 'dist' / f"fault-{test.CONFIG['REPORT_SUFFIX']}-{mode}-events.jsonl").write_text(events)
            result = {'fault': mode, 'ok': True, 'seconds': round(time.monotonic() - started, 2), 'transaction': tx}
            REPORT.append(result)
            print(json.dumps(result), flush=True)
        finally:
            try:
                test.ssh('nft delete table inet vlmcsd_test 2>/dev/null || true')
            except (OSError, subprocess.SubprocessError):
                pass
            if proc.poll() is None:
                proc.terminate()


if __name__ == '__main__':
    try:
        for mode in ['executor-killed', 'deadline', 'reboot']:
            fault(mode)
    finally:
        (ROOT / 'dist' / ('fault-results-' + test.CONFIG['REPORT_SUFFIX'] + '.json')).write_text(json.dumps(REPORT, indent=2) + '\n')
