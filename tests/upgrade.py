#!/usr/bin/env python3
"""Use a synthetic previous revision to verify downgrade and configuration restore."""
import json
import sys
import time
from pathlib import Path
import integration as test

ROOT = test.ROOT
OLD = ROOT / 'dist/test-old-release'


def main():
    old_manifest = json.loads((OLD / 'release.json').read_text())
    new_manifest = json.loads((ROOT / 'dist/release/release.json').read_text())
    old_version = next(p['version'] for p in old_manifest['packages'] if p['name'] == 'vlmcsd')
    new_version = next(p['version'] for p in new_manifest['packages'] if p['name'] == 'vlmcsd')
    assert 'QEMU' in json.loads(test.ssh('ubus call system board'))['model']
    installed = json.loads(test.ssh('apk query --from installed --format json --fields name,version "*"'))
    if any(p['name'] == 'vlmcsd' for p in installed):
        test.plan_apply('uninstall')
    oldplan = ROOT / 'dist' / 'old-plan.json'
    test.cli('deploy', 'plan', '--release', str(OLD), '--output', str(oldplan))
    test.cli('deploy', 'apply', '--plan', str(oldplan))
    test.ssh('uci set vlmcsd.main.port=1689; uci set vlmcsd.main.max_workers=2; uci set vlmcsd.main.timeout=45; uci commit vlmcsd; /etc/init.d/vlmcsd reload')
    time.sleep(1)
    before = test.ssh('sha256sum /etc/config/vlmcsd /etc/config/dhcp /etc/config/luci')
    test.ssh("nft add table inet vlmcsd_test; nft 'add chain inet vlmcsd_test output { type filter hook output priority -100; policy accept; }'; nft add rule inet vlmcsd_test output ip daddr 127.0.0.1 tcp dport 1689 drop")
    started = time.monotonic()
    try:
        plan = ROOT / 'dist' / 'upgrade-plan.json'
        test.cli('deploy', 'plan', '--rollback-dir', str(OLD), '--output', str(plan))
        result = test.cli('deploy', 'apply', '--plan', str(plan), good=False)
        txid = next(line.split(': ', 1)[1] for line in result.stdout.splitlines() if line.startswith('Transaction:'))
        assert test.ssh(f'cat /root/vlmcsd-deploy/{txid}/state').strip() == 'rolled_back'
        packages = json.loads(test.ssh('apk query --from installed --format json --fields name,version vlmcsd vlmcs luci-app-vlmcsd luci-i18n-vlmcsd-zh-cn'))
        assert len(packages) == 4 and all(p['version'] == old_version for p in packages), packages
        after = test.ssh('sha256sum /etc/config/vlmcsd /etc/config/dhcp /etc/config/luci')
        assert before == after, (before, after)
        status = test.status()
        assert status['running'] and status['port'] == 1689 and status['version'] == old_version, status
        assert all(endpoint.endswith(':1689') for endpoint in status['listeners']), status
        report = {'ok': True, 'test': 'failed upgrade restores exact previous packages and custom configuration', 'transaction': txid, 'seconds': round(time.monotonic() - started, 2)}
        (ROOT / 'dist/upgrade-results.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report), flush=True)
    finally:
        test.ssh('nft delete table inet vlmcsd_test 2>/dev/null || true')
    # Successful upgrade must also work, preserving the ownership receipt for uninstall.
    plan = ROOT / 'dist' / 'upgrade-success-plan.json'
    test.cli('deploy', 'plan', '--rollback-dir', str(OLD), '--output', str(plan))
    test.cli('deploy', 'apply', '--plan', str(plan))
    assert test.status()['version'] == new_version


if __name__ == '__main__':
    main()
