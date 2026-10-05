"""Run the actual wrapper with a harmless upstream stub and an owned real PTY."""
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import termios
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TerminalSizeTests(unittest.TestCase):
    def run_wrapper(self, rows, cols, tty=True):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            folder = Path(tmp)
            upstream = folder / 'upstream.sh'
            upstream.write_text('clashctl() { stty size 2>/dev/null || printf "NO_TTY\\n"; }\n')
            wrapper = folder / 'clashctl'
            text = (ROOT / 'container/clashctl').read_text()
            text = text.replace('source "$CLASHCTL_HOME/scripts/cmd/clashctl.sh"', 'source ' + repr(str(upstream)))
            wrapper.write_text(text)
            if not tty:
                p = subprocess.run(['bash', str(wrapper), 'node'], input='', capture_output=True, text=True, timeout=5)
                self.assertEqual(p.returncode, 0, p.stderr)
                return p.stdout.strip(), None
            def controlling_terminal():
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            p = None
            master, slave = pty.openpty()
            try:
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
                p = subprocess.Popen(['bash', str(wrapper), 'node'], stdin=slave,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=controlling_terminal)
                stdout, stderr = p.communicate(timeout=5)
                self.assertEqual(p.returncode, 0, stderr.decode())
                current = struct.unpack('HHHH', fcntl.ioctl(master, termios.TIOCGWINSZ, bytes(8)))[:2]
                return stdout.decode().strip(), current
            finally:
                try:
                    if p is not None:
                        try:
                            try:
                                if p.poll() is None:
                                    p.kill()
                            finally:
                                p.wait()
                        finally:
                            try:
                                if p.stdout is not None:
                                    p.stdout.close()
                            finally:
                                if p.stderr is not None:
                                    p.stderr.close()
                finally:
                    try:
                        os.close(master)
                    finally:
                        os.close(slave)

    def test_zero_dimensions_get_safe_defaults(self):
        self.assertEqual(self.run_wrapper(0, 0), ('30 120', (30, 120)))

    def test_valid_dimensions_are_preserved(self):
        self.assertEqual(self.run_wrapper(44, 132), ('44 132', (44, 132)))

    def test_only_missing_dimension_is_replaced(self):
        self.assertEqual(self.run_wrapper(0, 132), ('30 132', (30, 132)))
        self.assertEqual(self.run_wrapper(44, 0), ('44 120', (44, 120)))

    def test_noninteractive_invocation_does_not_require_tty(self):
        self.assertEqual(self.run_wrapper(0, 0, tty=False), ('NO_TTY', None))


if __name__ == '__main__':
    unittest.main()
