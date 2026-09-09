"""关闭按钮与标题栏回归；不连接设备，配置仅写临时文件。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import onnxruntime  # ORT 必须先于 Qt
import main
from PyQt6.QtTest import QTest
from src.config import load_config
from src import settings


class WindowBehaviorRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = main.QApplication.instance() or main.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'config.yaml'
        self.path.write_text('gui:\n  close_action: 最小化程序\nwork:\n  duration: 45分钟\n', encoding='utf-8')
        for name in ('_start_all', '_fill_devices', '_start_update_check'):
            p = patch.object(main.MainWindow, name)
            p.start()
            self.addCleanup(p.stop)
        p = patch('main.load_config', side_effect=lambda: load_config(self.path))
        p.start()
        self.addCleanup(p.stop)
        self.w = main.MainWindow()
        self.addCleanup(self.w.close)
        self.w.show()
        QTest.qWait(60)

    def test_title_close_minimizes_without_stopping_runner(self):
        runner = NS(poll=lambda: None)
        self.w._runner_proc = runner
        try:
            with patch.object(self.w, 'stop_runner') as stop:
                self.w.titleBar.closeBtn.click()
                QTest.qWait(60)
                self.assertTrue(self.w.isMinimized())
                self.assertIs(self.w._runner_proc, runner)
                self.assertTrue(self.w._scrcpy_watchdog.isActive())
                stop.assert_not_called()
        finally:
            self.w._runner_proc = None

    def test_title_close_exit_uses_existing_cleanup(self):
        self.path.write_text('gui:\n  close_action: 关闭程序\n', encoding='utf-8')
        with patch.object(self.w, 'stop_runner') as stop:
            self.w.titleBar.closeBtn.click()
            stop.assert_called_once()
            self.assertFalse(self.w.isVisible())
            self.assertFalse(self.w._scrcpy_watchdog.isActive())

    def test_programmatic_close_still_exits_with_minimize_selected(self):
        with patch.object(self.w, 'stop_runner') as stop:
            self.w.close()
            stop.assert_called_once()
            self.assertFalse(self.w.isVisible())

    def test_defaults_and_validation(self):
        self.path.write_text('gui: {}\n', encoding='utf-8')
        self.assertEqual(load_config(self.path).gui.close_action, '关闭程序')
        self.assertEqual(settings.validate_field('gui.close_action', '最小化程序'), (True, '最小化程序'))
        self.assertEqual(settings.validate_field('gui.close_action', 'invalid'), (False, '关闭程序'))

    def test_about_setting_round_trip_without_changing_other_fields(self):
        combo, choices = self.w._setting_widgets['gui.close_action']
        self.assertEqual(choices, ['关闭程序', '最小化程序'])
        # 同一读写接口，仅把文件路径改为临时配置。
        from ruamel.yaml import YAML
        yaml = YAML()
        def read():
            return yaml.load(self.path.read_text(encoding='utf-8'))
        def write(data):
            with self.path.open('w', encoding='utf-8') as stream:
                yaml.dump(data, stream)
        with patch.object(settings, 'load_raw', side_effect=read), patch.object(settings, 'save_raw', side_effect=write):
            self.w.load_settings()
            self.assertEqual(combo.currentText(), '最小化程序')
            combo.setCurrentText('关闭程序')
            self.assertEqual(load_config(self.path).gui.close_action, '关闭程序')
            self.assertEqual(read()['work']['duration'], '45分钟')
            combo.setCurrentText('最小化程序')
            self.w.load_settings()
            self.assertEqual(combo.currentText(), '最小化程序')

    @unittest.skipUnless(os.environ.get('QT_QPA_PLATFORM') == 'windows', '需要 Windows 原生窗口')
    def test_native_caption_absent_across_move_resize_and_restore(self):
        def check():
            style = main.win32gui.GetWindowLong(int(self.w.winId()), main.win32con.GWL_STYLE)
            self.assertFalse(style & main.win32con.WS_CAPTION)
            for flag in (main.win32con.WS_THICKFRAME, main.win32con.WS_MINIMIZEBOX, main.win32con.WS_MAXIMIZEBOX):
                self.assertTrue(style & flag)
        check()
        for x, y in ((30, 30), (110, 70), (50, 90)):
            self.w.move(x, y)
            self.w.resize(1000+x, 600+y)
            QTest.qWait(60)
            check()
        self.w.showMaximized()
        QTest.qWait(100)
        check()
        self.assertTrue(self.w.isMaximized())
        self.w.showNormal()
        self.w.showMinimized()
        self.w.showNormal()
        QTest.qWait(100)
        check()
        self.assertEqual(self.w.titleBar.height(), 32)
        self.assertEqual(self.w.navigationInterface.width(), 48)
        self.w.grab().save('runs/window-behavior-native.png')


if __name__ == '__main__':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    unittest.main(verbosity=2)
