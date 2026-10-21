#!/usr/bin/env python3
"""Run ARM64 binaries under unprivileged QEMU with the matching SDK loader/libs."""
import json
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from build_config import load_config
from sdk_layout import sdk_layout

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = load_config(require_upstream=True)
    if config['ELF_MACHINE'] != 'AArch64':
        raise ValueError('User-mode tests require an ARM64 target')
    sdk = ROOT / config['SDK_PATH']
    layout = sdk_layout(sdk)
    binary_dir = Path(layout['build_dir']) / ('vlmcsd-' + config['UPSTREAM_COMMIT']) / 'bin'
    toolchain = Path(layout['toolchain_dir'])
    loader = toolchain / 'lib/ld-musl-aarch64.so.1'
    if not loader.is_file():
        raise ValueError('Missing matching SDK musl loader: ' + str(loader))
    qemu = ['qemu-aarch64', '-cpu', config['QEMU_CPU'], '-L', str(toolchain)]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    address = '127.0.0.1:' + str(port)
    results = []
    with tempfile.TemporaryDirectory(prefix='vlmcsd-user-') as directory:
        log = Path(directory) / 'server.log'
        with log.open('w') as output:
            server = subprocess.Popen(qemu + [str(binary_dir / 'vlmcsd'), '-D', '-e', '-L', address],
                                      stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 10
                while True:
                    if server.poll() is not None:
                        raise RuntimeError(log.read_text())
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(log.read_text())
                        time.sleep(.1)
                for protocol in [4, 5, 6]:
                    result = subprocess.run(qemu + [str(binary_dir / 'vlmcs'), '-v', '-' + str(protocol), '-n', '1', address],
                                            text=True, capture_output=True, timeout=15)
                    if result.returncode or 'Response from KMS server' not in result.stdout:
                        raise RuntimeError(result.stdout + result.stderr)
                    results.append({'protocol': protocol, 'ok': True})
                # The ARM timeout tool controls a real child and must return timeout status.
                started = time.monotonic()
                result = subprocess.run(qemu + [str(binary_dir / 'vlmcsd-timeout'), '-k', '1', '1', '/bin/sleep', '30'],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode != 124 or time.monotonic() - started >= 5:
                    raise RuntimeError('Timeout helper failed: ' + str(result.returncode))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    report = {'ok': True, 'openwrt': config['OPENWRT_VERSION'], 'target': config['OPENWRT_TARGET'],
              'arch': config['OPENWRT_ARCH'], 'cpu': config['QEMU_CPU'], 'protocols': results, 'timeout': True}
    path = ROOT / ('dist/user-mode-' + config['REPORT_SUFFIX'] + '.json')
    path.write_text(json.dumps(report, indent=2) + '\n')
    print('PASS:', path)


if __name__ == '__main__':
    main()
