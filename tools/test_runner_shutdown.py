"""Shutdown failures and real Windows process trees; no phone or live config."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import onnxruntime  # before Qt
import main
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QPushButton


@contextmanager
def invalid_stdin():
    """Give this test process the same invalid Win32 stdin seen in the GUI log."""
    import ctypes
    import _winapi
    old = _winapi.GetStdHandle(_winapi.STD_INPUT_HANDLE)
    set_handle = ctypes.windll.kernel32.SetStdHandle
    set_handle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
    set_handle.restype = ctypes.c_int
    assert set_handle(_winapi.STD_INPUT_HANDLE & 0xffffffff, ctypes.c_void_p(-1))
    try:
        yield
    finally:
        assert set_handle(_winapi.STD_INPUT_HANDLE & 0xffffffff, old)


class RunnerShutdown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = main.QApplication.instance() or main.QApplication([])

    def setUp(self):
        self.proc = Mock(pid=12345)
        self.proc.poll.return_value = None
        self.owner = NS(_runner_proc=self.proc, _runner_started_at=123)
        self.messages = []
        p = patch.object(main, 'log', side_effect=self.messages.append)
        p.start()
        self.addCleanup(p.stop)

    def stop(self):
        return main.MainWindow.stop_runner(self.owner)

    def test_system_launch_error_from_button_does_not_escape(self):
        caught = []
        button = QPushButton()
        button.clicked.connect(lambda: self.stop())
        with patch.object(sys, 'excepthook', side_effect=lambda *args: caught.append(args)), \
             patch.object(main.subprocess, 'run', side_effect=OSError(6, 'invalid handle')):
            button.click()
        self.assertEqual(caught, [])
        self.assertIs(self.owner._runner_proc, self.proc)
        self.assertEqual(self.owner._runner_started_at, 123)
        self.assertIn('OSError', self.messages[-1])

    def test_command_timeout_is_reported_and_keeps_state(self):
        with patch.object(main.subprocess, 'run', side_effect=subprocess.TimeoutExpired('taskkill', 10)):
            self.assertFalse(self.stop())
        self.assertEqual(self.owner._runner_started_at, 123)
        self.assertIn('TimeoutExpired', self.messages[-1])

    def test_nonzero_command_does_not_claim_stopped(self):
        with patch.object(main.subprocess, 'run', return_value=NS(returncode=1, stderr=b'denied', stdout=b'')):
            self.assertFalse(self.stop())
        self.proc.kill.assert_not_called()
        self.assertIn('denied', self.messages[-1])

    def test_wait_timeout_is_handled(self):
        self.proc.wait.side_effect = subprocess.TimeoutExpired('runner', 5)
        with patch.object(main.subprocess, 'run', return_value=NS(returncode=0)):
            self.assertFalse(self.stop())
        self.assertEqual(self.owner._runner_started_at, 123)

    def test_success_checks_exit_and_uses_explicit_stdio(self):
        self.proc.wait.side_effect = lambda **kw: setattr(self.proc.poll, 'return_value', 0)
        with patch.object(main.subprocess, 'run', return_value=NS(returncode=0)) as run:
            self.assertTrue(self.stop())
        self.assertIsNone(self.owner._runner_started_at)
        cmd = run.call_args.args[0]
        self.assertTrue(Path(cmd[0]).is_absolute())
        self.assertIn('/T', cmd)
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_exit_race_is_success(self):
        self.proc.poll.side_effect = [None, 0, 0]
        with patch.object(main.subprocess, 'run', return_value=NS(returncode=128)):
            self.assertTrue(self.stop())

    def test_no_process_is_success(self):
        self.owner._runner_proc = None
        with patch.object(main.subprocess, 'run') as run:
            self.assertTrue(self.stop())
        run.assert_not_called()

    def test_invalid_stdin_reproduces_original_winerror_6(self):
        with invalid_stdin(), self.assertRaises(OSError) as raised:
            subprocess.Popen([sys.executable, '-c', 'pass'], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(raised.exception.winerror, 6)

    def test_three_start_stop_cycles_with_invalid_stdin(self):
        real_popen = subprocess.Popen
        self.owner._runner_proc = None
        self.owner._read_runner_logs = lambda proc: None
        self.owner._log_queue = main.queue.Queue()
        with invalid_stdin():
            for _ in range(3):
                with patch.object(main.subprocess, 'Popen', side_effect=lambda cmd, **kw:
                                  real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kw)), \
                     patch.object(main.threading, 'Thread'):
                    main.MainWindow.start_runner(self.owner)
                proc = self.owner._runner_proc
                try:
                    self.assertIsNone(proc.poll())
                    self.assertTrue(self.stop(), self.messages)
                    self.assertIsNotNone(proc.poll())
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
                    proc.stdout.close()

    def test_both_mirror_modes_with_invalid_stdin(self):
        real_popen = subprocess.Popen
        with invalid_stdin():
            for start in (main.start_scrcpy, main.start_scrcpy_screen_off):
                with patch.object(main.subprocess, 'Popen', side_effect=lambda cmd, **kw:
                                  real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kw)), \
                     patch.object(main, 'load_config', return_value=NS(adb=NS(device_serial='test-no-device'))), \
                     patch.object(main.time, 'sleep'):
                    proc = start()
                self.assertIsNotNone(proc)
                try:
                    self.assertIsNone(proc.poll())
                finally:
                    proc.kill()
                    proc.wait(timeout=5)

    def test_device_helpers_with_invalid_stdin(self):
        from src.adb.device import Device
        from src.opener import _adb_run
        from src.u2dev import MiniTouchSession
        real_popen = subprocess.Popen
        device = Device('unused-test-adb', 'test-device')
        with invalid_stdin(), patch.object(subprocess, 'Popen', side_effect=lambda cmd, **kw:
                real_popen([sys.executable, '-c', 'print("List of devices attached\\ntest-device\\tdevice")'], **kw)):
            self.assertEqual(device._run('get-state').returncode, 0)
            self.assertEqual(device.online_devices(), ['test-device'])
            device.connect_remote('test-host:5555')
            self.assertEqual(_adb_run('unused-test-adb', 'test-device', 'get-state').returncode, 0)
            session = NS(dev=NS(adb=device))
            self.assertEqual(MiniTouchSession._adb(session, 'get-state').returncode, 0)

    def test_failed_stop_blocks_restart(self):
        self.owner.stop_runner = Mock(return_value=False)
        self.owner.start_runner = Mock()
        with patch.object(main.QTimer, 'singleShot') as timer:
            main.MainWindow._restart_runner(self.owner)
        timer.assert_not_called()

    def test_failed_stop_blocks_close_without_disabling_timers(self):
        self.owner.stop_runner = Mock(return_value=False)
        for name in ('_restart_timer', '_scrcpy_watchdog', '_embed_timer'):
            setattr(self.owner, name, Mock())
        event = Mock()
        main.MainWindow.closeEvent(self.owner, event)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        self.owner._scrcpy_watchdog.stop.assert_not_called()

    def test_failed_stop_blocks_recovery(self):
        self.owner.stop_runner = Mock(return_value=False)
        for name in ('_btn_manual_recover', 'btn_start', 'btn_stop'):
            setattr(self.owner, name, Mock())
        with patch.object(main.threading, 'Thread') as thread:
            main.MainWindow._manual_recover(self.owner)
        thread.assert_not_called()
        self.assertFalse(self.owner._recovering)
        self.owner._btn_manual_recover.setEnabled.assert_called_with(True)

    def test_failed_stop_still_blocks_profile_switch(self):
        self.owner.profile_combo = NS(currentData=lambda: 'a' * 32)
        self.owner._recovering = False
        self.owner._test_lock = NS(locked=lambda: False)
        self.owner._profiles = Mock()
        self.owner._profile_error = Mock()
        main.MainWindow._switch_profile(self.owner)
        self.owner._profiles.select.assert_not_called()
        self.owner._profile_error.assert_called_once()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process tree')
    def test_real_process_and_child_both_stop(self):
        import win32api
        import win32con
        import win32event
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / 'child.txt'
            script = ('import subprocess,sys,time; from pathlib import Path; '
                      'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
                      'creationflags=subprocess.CREATE_NO_WINDOW); '
                      'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)')
            proc = subprocess.Popen([sys.executable, '-c', script, str(ready)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            child_handle = None
            try:
                deadline = time.monotonic() + 8
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(ready.exists(), 'test child failed to start')
                child_handle = win32api.OpenProcess(win32con.SYNCHRONIZE | win32con.PROCESS_TERMINATE,
                                                    False, int(ready.read_text()))
                self.owner._runner_proc = proc
                self.assertTrue(self.stop(), self.messages)
                self.assertIsNotNone(proc.poll())
                self.assertEqual(win32event.WaitForSingleObject(child_handle, 5000), win32event.WAIT_OBJECT_0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                if child_handle is not None:
                    if win32event.WaitForSingleObject(child_handle, 0) != win32event.WAIT_OBJECT_0:
                        win32api.TerminateProcess(child_handle, 1)
                    child_handle.Close()

    def test_unexpected_qt_exception_is_saved_without_process_abort(self):
        from src import gui_diagnostics
        with tempfile.TemporaryDirectory() as directory:
            previous_hook = sys.excepthook
            try:
                gui_diagnostics.install_gui_diagnostics(Path(directory), self.messages.append)
                button = QPushButton()
                def fail():
                    raise ValueError('controlled GUI regression')
                button.clicked.connect(fail)
                button.click()
                log_file = next(Path(directory).glob('gui-errors-*.log'))
                self.assertIn('ValueError: controlled GUI regression', log_file.read_text(encoding='utf-8'))
            finally:
                sys.excepthook = previous_hook
                gui_diagnostics.faulthandler.disable()
                if gui_diagnostics._crash_stream:
                    gui_diagnostics._crash_stream.close()
                    gui_diagnostics._crash_stream = None


if __name__ == '__main__':
    unittest.main(verbosity=2)
