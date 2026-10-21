#!/usr/bin/env python3
"""Native IPK/APK and full LuCI integration against the fixed disposable QEMU guest."""
import hashlib
import io
import json
import shlex
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_config import load_config

CONFIG = load_config(require_upstream=True)
VM = ROOT / CONFIG['VM_PATH']

SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3', '-o', 'StrictHostKeyChecking=accept-new',
       '-o', f'UserKnownHostsFile={VM}/vm_known_hosts', '-i', f'{VM}/vm_id',
       '-p', '22222', 'root@127.0.0.1']
RESULTS = []


def ssh(command, *, data=None, timeout=30):
    return subprocess.check_output(SSH + [command], input=data, timeout=timeout).decode()


def rpc(method, args=None):
    return json.loads(ssh('ubus call luci.vlmcsd ' + method + ' ' + shlex.quote(json.dumps(args or {}))))


def wait_running(expected=True):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        s = rpc('status')
        if bool(s.get('running')) == expected:
            return s
        time.sleep(.5)
    raise AssertionError(s)


def check(name, function):
    start = time.monotonic()
    details = function()
    RESULTS.append({'test': name, 'ok': True, 'seconds': round(time.monotonic() - start, 2), 'details': details})
    print('PASS:', name, flush=True)


def main(config):
    deadline = time.monotonic() + 120
    while True:
        try:
            board = json.loads(ssh('ubus call system board'))
            break
        except (subprocess.SubprocessError, ValueError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    assert ('QEMU' in board['model'] or (board['model'] == 'linux,dummy-virt' and board['release']['target'] == 'armsr/armv8')), 'Refusing a non-QEMU device'
    assert board['release']['version'] == config['OPENWRT_VERSION'], board
    fmt = config['PACKAGE_FORMAT']
    assert board['release']['target'] == ('armsr/armv8' if config['OPENWRT_TARGET'] == 'rockchip/armv8' else config['OPENWRT_TARGET'])
    assert ssh(". /etc/openwrt_release; echo $DISTRIB_ARCH").strip() == config['OPENWRT_ARCH']
    assert ssh('command -v ' + config['PACKAGE_MANAGER']).strip() in {'/bin/' + config['PACKAGE_MANAGER'], '/usr/bin/' + config['PACKAGE_MANAGER']}
    release = ROOT / config['RELEASE_PATH']
    manifest = json.loads((release / 'release.json').read_text())
    assert manifest['openwrt'] == board['release']['version'] and manifest['package_format'] == fmt
    for entry in manifest['files']:
        assert hashlib.sha256((release / entry['file']).read_bytes()).hexdigest() == entry['sha256']
    if fmt == 'ipk':
        subprocess.check_call(['openssl', 'dgst', '-sha256', '-verify', str(release / 'vlmcsd.pem'),
                           '-signature', str(release / 'SHA256SUMS.sig'), str(release / 'SHA256SUMS')])

    def install():
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for package in manifest['packages']:
                archive.add(release / package['file'], arcname=package['file'])
        ssh('mkdir -p /tmp/vlmcsd-ipk; tar -xzf - -C /tmp/vlmcsd-ipk', data=buffer.getvalue())
        if fmt == 'ipk':
            command = 'opkg update && opkg install /tmp/vlmcsd-ipk/*.ipk'
        else:
            ssh('cat > /etc/apk/keys/vlmcsd.pem', data=(release / 'vlmcsd.pem').read_bytes())
            command = 'apk update && apk add /tmp/vlmcsd-ipk/*.apk'
        log = ssh(command, timeout=120)
        (ROOT / 'dist' / ('ipk-install-' + config['REPORT_SUFFIX'] + '.log')).write_text(log)
        ssh('/etc/init.d/rpcd restart')
        time.sleep(1)
        assert not rpc('status')['running'], 'Fresh packages should default to disabled'
        ssh('uci set vlmcsd.main.enabled=1; uci commit vlmcsd; /etc/init.d/vlmcsd enable; /etc/init.d/vlmcsd start')
        return wait_running()
    check('native package install, disabled default and explicit enable', install)

    def service():
        s = wait_running()
        assert len(s['listeners']) == 2, s
        assert ssh(f"sed -n 's/^Uid:[[:space:]]*//p' /proc/{s['pid']}/status").split()[0] != '0'
        assert s['version'] == config['PACKAGE_VERSION']
        assert s['source_commit'] == config['UPSTREAM_COMMIT']
        for protocol in [4, 5, 6]:
            result = rpc('selftest', {'protocol': protocol})
            assert result['ok'] and 'Response from KMS server' in result['output'], result
        return s
    check('non-root listeners and v4/v5/v6 diagnostics', service)

    def controls():
        for action in ['stop', 'start', 'restart', 'reload']:
            result = rpc('action', {'action': action})
            assert result['ok'], result
            wait_running(action != 'stop')
        assert rpc('logs')['ok']
        assert not rpc('action', {'action': 'start;reboot'})['ok']
        assert not rpc('selftest', {'protocol': 3})['ok']
        ssh('uci set vlmcsd.main.max_workers=999; uci commit vlmcsd')
        result = rpc('action', {'action': 'reload'})
        assert not result['ok'] and rpc('status')['running'], result
        ssh('uci set vlmcsd.main.max_workers=4; uci commit vlmcsd')
    check('LuCI actions, logs, invalid RPC/config rejection', controls)

    def http_and_acl():
        session = json.loads(ssh('ubus call session login \'{"username":"root","password":""}\''))['ubus_rpc_session']
        def http_rpc(obj, method, arguments):
            request = urllib.request.Request('http://127.0.0.1:18080/ubus',
                data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'call',
                                 'params': [session, obj, method, arguments]}).encode(),
                headers={'Content-Type': 'application/json'})
            return json.load(urllib.request.urlopen(request, timeout=15))['result']
        result = http_rpc('luci.vlmcsd', 'status', {})
        assert result[0] == 0 and result[1]['running'], result
        result = http_rpc('uci', 'set', {'config': 'vlmcsd', 'section': 'main', 'values': {'verbose': '1'}})
        assert result[0] == 0, result
        # Same session-scoped UCI path used by the LuCI form; reset without persisting.
        ssh('ubus call uci revert ' + shlex.quote(json.dumps({'ubus_rpc_session': session, 'config': 'vlmcsd'})))
        for method in ['action', 'selftest']:
            access = json.loads(ssh('ubus call session access ' + shlex.quote(json.dumps({
                'ubus_rpc_session': session, 'scope': 'ubus', 'object': 'luci.vlmcsd', 'function': method}))))
            assert access['access'], access
        anonymous = json.loads(ssh('ubus call session create'))['ubus_rpc_session']
        access = json.loads(ssh('ubus call session access ' + shlex.quote(json.dumps({
            'ubus_rpc_session': anonymous, 'scope': 'ubus', 'object': 'luci.vlmcsd', 'function': 'action'}))))
        assert not access['access']
        for url, needle in [('/luci-static/resources/view/vlmcsd/overview.js', b'luci.vlmcsd')]:
            with urllib.request.urlopen('http://127.0.0.1:18080' + url, timeout=15) as response:
                body = response.read()
                assert body and (not needle or needle in body)
        assert ssh('test -s /usr/lib/lua/luci/i18n/vlmcsd.zh-cn.lmo && echo translated').strip() == 'translated'
        acl = json.loads(ssh('cat /usr/share/rpcd/acl.d/luci-app-vlmcsd.json'))
        assert 'luci-app-vlmcsd' in acl
        return {'http_rpc': True, 'configuration_write_acl': True, 'anonymous_denied': True,
                'javascript_and_chinese_translation': True}
    check('authenticated LuCI HTTP RPC, UCI ACL and translation assets', http_and_acl)

    def dns():
        ssh('uci set vlmcsd.main.dns=1; uci commit vlmcsd; /etc/init.d/vlmcsd reload')
        assert '_vlmcs._tcp.lan' in ssh('uci show dhcp.vlmcsd_srv')
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
    check('DNS SRV publish and revoke', dns)

    def reboot():
        old_boot = ssh('cat /proc/sys/kernel/random/boot_id').strip()
        ssh('sync')
        # Cold power cycling avoids firmware/TCG warm-reset behavior and retains the guest disk.
        subprocess.check_call(['docker', 'restart', '-t', '5', 'vlmcsd-test-vm'],
                              stdout=subprocess.DEVNULL, timeout=20)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            time.sleep(2)
            try:
                new_boot = ssh('cat /proc/sys/kernel/random/boot_id').strip()
                if new_boot != old_boot and rpc('status')['running']:
                    return {'boot_changed': True, 'service_started': True}
            except (subprocess.SubprocessError, ValueError):
                pass
        raise AssertionError('Guest failed to cold-boot/start service within 120 seconds')
    check('power-cycle boot autostart', reboot)

    def uninstall():
        before = ssh('sha256sum /etc/config/vlmcsd').split()[0]
        ssh('/etc/init.d/vlmcsd stop; /etc/init.d/vlmcsd disable; ' + ('opkg remove' if fmt == 'ipk' else 'apk del') + ' luci-i18n-vlmcsd-zh-cn luci-app-vlmcsd vlmcs vlmcsd; /etc/init.d/rpcd restart')
        assert ssh('sha256sum /etc/config/vlmcsd').split()[0] == before
        assert ssh('test ! -x /usr/sbin/vlmcsd && echo removed').strip() == 'removed'
        assert not ssh('uci -q show dhcp.vlmcsd_srv || true').strip()
    check('native uninstall retains modified configuration', uninstall)
    return board


if __name__ == '__main__':
    config = load_config(require_upstream=True)
    report = ROOT / 'dist' / ('ipk-integration-' + config['REPORT_SUFFIX'] + '.json')
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        board = main(config)
        report.write_text(json.dumps({'ok': True, 'board': board, 'results': RESULTS}, indent=2) + '\n')
    except Exception as exc:
        report.write_text(json.dumps({'ok': False, 'error': str(exc), 'results': RESULTS}, indent=2) + '\n')
        raise
