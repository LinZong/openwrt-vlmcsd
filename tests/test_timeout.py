"""Run with a native C compiler (CI/Linux or macOS) to test cancellation semantics."""
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('cc'), 'C compiler unavailable')
class ProcessGroupTimeout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = str(Path(cls.temp.name) / 'timeout')
        source = Path(__file__).parents[1] / 'package/vlmcsd/src/vlmcsd-timeout.c'
        subprocess.check_call(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(source), '-o', cls.binary])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_preserves_exit_status(self):
        self.assertEqual(subprocess.call([self.binary, '2', 'sh', '-c', 'exit 7']), 7)

    def test_timeout_does_not_wait_full_deployment_budget(self):
        started = time.monotonic()
        self.assertEqual(subprocess.call([self.binary, '-k', '0', '1', 'sleep', '30']), 124)
        self.assertLess(time.monotonic() - started, 2)

    def test_terminates_descendants(self):
        pidfile = Path(self.temp.name) / 'child'
        proc = subprocess.Popen([self.binary, '-k', '0', '1', 'sh', '-c', f'sleep 30 & echo $! > {pidfile}; wait'])
        proc.wait(timeout=3)
        self.assertEqual(proc.returncode, 124)
        pid = int(pidfile.read_text())
        # Linux may briefly retain an orphan zombie; it must not be running.
        path = Path(f'/proc/{pid}/stat')
        if path.exists():
            self.assertEqual(path.read_text().split()[2], 'Z')
        elif os.name == 'posix':
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                self.fail('Descendant survived timeout')

    def test_external_cancellation_is_forwarded(self):
        ready = Path(self.temp.name) / 'ready'
        proc = subprocess.Popen([self.binary, '30', 'sh', '-c', f'echo ready > {ready}; sleep 30'])
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists())
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=2), 128 + signal.SIGTERM)


if __name__ == '__main__':
    unittest.main()
