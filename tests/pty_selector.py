"""Bounded PTY search/Enter driver for isolated real selector acceptance."""
import fcntl
import os
import pty
import select
import struct
import subprocess
import termios
import time
import tty


def choose(command, rendered, query, rows=24, cols=100, controlling=False):
    def controlling_terminal():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    process = None
    master, slave = pty.openpty()
    try:
        tty.setraw(slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
            preexec_fn=controlling_terminal if controlling else None)
        os.close(slave)
        slave = None
        output = b''
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and rendered not in output:
            if select.select([master], [], [], .2)[0]:
                chunk = os.read(master, 65536)
                output += chunk
                if b'\x1b[6n' in chunk:
                    os.write(master, b'\x1b[1;1R')
        assert rendered in output, 'Selector did not render in PTY'
        os.write(master, query)
        deadline = time.monotonic() + 20
        entered = False
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], .2)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                output += chunk
                if b'\x1b[6n' in chunk:
                    os.write(master, b'\x1b[1;1R')
                if not entered and query in chunk:
                    time.sleep(.2)
                    os.write(master, b'\r')
                    entered = True
        assert entered, 'Selector search did not render'
        assert process.wait(timeout=2) == 0, 'Selector exited unsuccessfully'
        dimensions = struct.unpack('HHHH', fcntl.ioctl(master, termios.TIOCGWINSZ, bytes(8)))[:2]
        return output, dimensions
    finally:
        try:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                finally:
                    process.wait()
        finally:
            try:
                os.close(master)
            finally:
                if slave is not None:
                    os.close(slave)
