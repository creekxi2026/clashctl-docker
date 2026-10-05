"""Deterministic resource-failure injection; no containers or external services."""
import errno
import os
import pty
import subprocess
import unittest
from unittest.mock import Mock, call, patch

import pty_selector
import test_terminal_size


class PtyCleanupTests(unittest.TestCase):
    def owned_pty(self):
        descriptors = pty.openpty()
        for fd in descriptors:
            self.addCleanup(self.close_if_open, fd)
        return descriptors

    @staticmethod
    def close_if_open(fd):
        try:
            os.close(fd)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise

    def assert_closed(self, descriptors):
        for fd in descriptors:
            with self.assertRaises(OSError) as caught:
                os.fstat(fd)
            self.assertEqual(caught.exception.errno, errno.EBADF)

    def invoke(self, module):
        if module is pty_selector:
            return module.choose(['unused'], b'ready', b'query')
        return module.TerminalSizeTests().run_wrapper(0, 0)

    def test_configuration_failures_close_both_pty_fds(self):
        for module, target in (
            (pty_selector, 'tty.setraw'),
            (pty_selector, 'fcntl.ioctl'),
            (test_terminal_size, 'fcntl.ioctl'),
        ):
            with self.subTest(module=module.__name__, target=target):
                descriptors = self.owned_pty()
                with patch.object(module.pty, 'openpty', return_value=descriptors), \
                     patch(module.__name__ + '.' + target, side_effect=OSError('configuration failed')), \
                     patch.object(module.subprocess, 'Popen') as spawn:
                    with self.assertRaisesRegex(OSError, 'configuration failed'):
                        self.invoke(module)
                    spawn.assert_not_called()
                self.assert_closed(descriptors)

    def test_spawn_failures_close_both_pty_fds(self):
        for module in (pty_selector, test_terminal_size):
            with self.subTest(module=module.__name__):
                descriptors = self.owned_pty()
                with patch.object(module.pty, 'openpty', return_value=descriptors), \
                     patch.object(module.subprocess, 'Popen', side_effect=OSError('spawn failed')):
                    with self.assertRaisesRegex(OSError, 'spawn failed'):
                        self.invoke(module)
                self.assert_closed(descriptors)

    def test_communicate_failures_reap_child_and_close_pipes_and_pty(self):
        for failure in (subprocess.TimeoutExpired('unused', 5), OSError('communicate failed')):
            with self.subTest(failure=type(failure).__name__):
                descriptors = self.owned_pty()
                read_fd, write_fd = os.pipe()
                child = Mock()
                child.stdout = os.fdopen(read_fd, 'rb')
                child.stderr = os.fdopen(write_fd, 'wb')
                self.addCleanup(child.stdout.close)
                self.addCleanup(child.stderr.close)
                child.poll.return_value = None
                child.communicate.side_effect = failure
                with patch.object(test_terminal_size.pty, 'openpty', return_value=descriptors), \
                     patch.object(test_terminal_size.subprocess, 'Popen', return_value=child):
                    with self.assertRaises(type(failure)) as caught:
                        self.invoke(test_terminal_size)
                    self.assertIs(caught.exception, failure)
                self.assert_closed(descriptors)
                with self.subTest(resource='stdout'):
                    self.assertTrue(child.stdout.closed, 'owned stdout pipe leaked')
                with self.subTest(resource='stderr'):
                    self.assertTrue(child.stderr.closed, 'owned stderr pipe leaked')
                with self.subTest(resource='child termination'):
                    child.kill.assert_called_once_with()
                with self.subTest(resource='child reaping'):
                    child.wait.assert_called_once_with()
                if call.kill() in child.method_calls and call.wait() in child.method_calls:
                    self.assertLess(child.method_calls.index(call.kill()),
                                    child.method_calls.index(call.wait()))
