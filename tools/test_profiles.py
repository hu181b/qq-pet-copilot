"""Profile isolation and native mirror regression; uses temporary data only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import onnxruntime
from src.profiles import ProfileStore, resolve_profile


class ProfilesRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main
        cls.app = main.QApplication.instance() or main.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ProfileStore(self.root)
        (self.root / 'config.yaml').write_text('work:\n  duration: 10分钟\n', encoding='utf-8')

    def test_default_preserves_existing_data(self):
        self.assertEqual(self.store.directory('default'), self.root)
        self.assertEqual(self.store.read()['active'], 'default')
        self.assertFalse(self.store.file.exists())

    def test_create_copies_settings_only_and_rename_keeps_id(self):
        (self.root / 'runs').mkdir()
        (self.root / 'runs' / 'work_progress.json').write_text('{"learned":7}')
        key = self.store.create('小号 / 夜间', 'default')
        self.assertEqual((self.store.directory(key) / 'config.yaml').read_bytes(), (self.root / 'config.yaml').read_bytes())
        self.assertFalse((self.store.directory(key) / 'runs').exists())
        self.store.rename(key, '周末配置')
        self.store.select(key)
        self.assertEqual(self.store.read()['profiles'][key], '周末配置')
        with patch.dict(os.environ, {'QQPET_PROFILE_ID': key}):
            self.assertEqual(resolve_profile(self.root), (key, self.store.directory(key)))

    def test_invalid_duplicate_and_traversal(self):
        for name in (' ', 'a'*41, '换\n行', '默认配置'):
            with self.assertRaises(ValueError):
                self.store.create(name, 'default')
        for key in ('../elsewhere', 'C:/x', '', 'a'*31):
            with self.assertRaises(ValueError):
                self.store.directory(key)

    def test_runner_environment_pins_selection(self):
        key = self.store.create('第二套', 'default')
        self.store.select(key)
        with patch.dict(os.environ, {'QQPET_PROFILE_ID': 'default'}):
            self.assertEqual(resolve_profile(self.root)[0], 'default')

    def test_missing_config_refuses_switch(self):
        key = self.store.create('第二套', 'default')
        (self.store.directory(key) / 'config.yaml').unlink()
        with self.assertRaises(ValueError):
            self.store.select(key)
        self.assertEqual(self.store.read()['active'], 'default')

    def test_subprocess_data_and_settings_are_isolated(self):
        keys = ['default', self.store.create('第二套', 'default')]
        script = '''
import sys, pathlib, json
sys.frozen = True
sys.executable = str(pathlib.Path(sys.argv[1]) / 'test.exe')
sys._MEIPASS = sys.argv[2]
from src import config, settings, progress, status_cache, queue_status
config.load_config()
raw = settings.load_raw()
raw['work']['duration'] = sys.argv[3]
settings.save_raw(raw)
progress.WORK_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
progress.WORK_PROGRESS_FILE.write_text(json.dumps({'marker':sys.argv[3]}))
status_cache.update_status(energy=int(sys.argv[4]))
queue_status.save_queue_status({'current':sys.argv[3]})
progress.log('isolation '+sys.argv[3])
assert config.load_config().work.duration == sys.argv[3]
assert config.APP_ROOT != config.DATA_ROOT or config.PROFILE_ID == 'default'
'''
        for key, duration, energy in zip(keys, ['10分钟', '45分钟'], ['10', '45']):
            proc = subprocess.run([sys.executable, '-c', script, str(self.root), str(Path.cwd()), duration, energy],
                                  env=dict(os.environ, QQPET_PROFILE_ID=key, PYTHONIOENCODING='utf-8'),
                                  capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(proc.returncode, 0, proc.stderr)
        for key, duration in zip(keys, ['10分钟', '45分钟']):
            base = self.store.directory(key)
            self.assertEqual(json.loads((base/'runs/work_progress.json').read_text())['marker'], duration)
            self.assertEqual(json.loads((base/'runs/queue_status.json').read_text(encoding='utf-8'))['current'], duration)
            self.assertEqual(len(list((base/'runs/logs').glob('*.log'))), 1)

    def test_scrcpy_foreground_pid_even_with_different_root(self):
        import main
        w = NS(winId=lambda:100, isActiveWindow=lambda:False, _scrcpy_proc=NS(pid=777, poll=lambda:None))
        with patch('main.win32gui.GetForegroundWindow', return_value=200), patch('main.win32gui.GetAncestor', side_effect=lambda h,f:h), patch('main.win32process.GetWindowThreadProcessId', return_value=(1,777)):
            self.assertTrue(main.window_is_foreground(w))
        with patch('main.win32gui.GetForegroundWindow', return_value=200), patch('main.win32gui.GetAncestor', side_effect=lambda h,f:h), patch('main.win32process.GetWindowThreadProcessId', return_value=(1,888)):
            self.assertFalse(main.window_is_foreground(w))

    def test_embed_sets_child_style_and_unchanged_size_does_not_repaint(self):
        import main
        app = main.QApplication.instance() or main.QApplication([])
        view = main.ScrcpyContainer()
        with patch.multiple(main.win32gui, SetParent=Mock(), GetClientRect=Mock(return_value=(0,0,472,1024)), GetWindowLong=Mock(return_value=main.win32con.WS_POPUP), SetWindowLong=Mock(), SetWindowPos=Mock()):
            view.embed(123)
            style = main.win32gui.SetWindowLong.call_args.args[2]
            self.assertTrue(style & main.win32con.WS_CHILD)
            self.assertFalse(style & main.win32con.WS_POPUP)
            count = main.win32gui.SetWindowPos.call_count
            for _ in range(20):
                view._fit()
            self.assertEqual(main.win32gui.SetWindowPos.call_count, count)
        view.set_hwnd(None)
        view.close()

    def test_gui_profile_actions_and_running_guard(self):
        import main
        app = main.QApplication.instance() or main.QApplication([])
        with patch('main.APP_ROOT', self.root), patch.object(main.MainWindow, '_start_all'), patch.object(main.MainWindow, '_fill_devices'), patch.object(main.MainWindow, '_start_update_check'):
            w = main.MainWindow()
            with patch('main.QInputDialog.getText', return_value=('测试配置', True)):
                w._new_profile()
            key = w.profile_combo.currentData()
            self.assertNotEqual(key, 'default')
            with patch('main.QInputDialog.getText', return_value=('新名称', True)):
                w._rename_profile()
            self.assertEqual(w.profile_combo.currentText(), '新名称')
            w._runner_proc = NS(poll=lambda:None)
            with patch.object(w, '_profile_error') as error:
                w._switch_profile()
                error.assert_called_once()
            self.assertEqual(self.store.read()['active'], 'default')
            w._runner_proc = None
            with patch.object(w, 'close') as close:
                w._switch_profile()
                close.assert_called_once()
            self.assertEqual(w._next_profile, key)
            self.assertEqual(self.store.read()['active'], key)
            w.close()

    def test_schedule_requires_click_before_edit_and_preserves_save(self):
        import main
        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QWheelEvent
        from PyQt6.QtTest import QTest
        with patch.object(main.MainWindow, '_start_all'), patch.object(main.MainWindow, '_fill_devices'), patch.object(main.MainWindow, '_start_update_check'), patch.object(main.MainWindow, '_save_schedule_values') as save:
            w = main.MainWindow()
            w.show()
            w.stackedWidget.setCurrentIndex(1)
            QTest.qWait(600)
            w._refresh_schedule()
            try:
                rows = w._schedule_rows
                for key, col, text in [('care',2,'123'), ('pk',2,'05:30'), ('care',3,'08:00-20:00')]:
                    row = rows.index(key)
                    cell = w.schedule_table.cellWidget(row,col)
                    self.assertIsInstance(cell, main.ClickToEdit)
                    before = cell.display.text()
                    save.reset_mock()
                    self.assertFalse(cell.editor.isVisible())
                    main.QApplication.sendEvent(cell.display, QEvent(QEvent.Type.Enter))
                    wheel = QWheelEvent(QPointF(5,5),QPointF(5,5),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
                    main.QApplication.sendEvent(cell.display,wheel)
                    self.assertEqual(cell.display.text(),before)
                    self.assertFalse(cell.editor.isVisible())
                    save.assert_not_called()
                    QTest.mouseClick(cell.display,Qt.MouseButton.LeftButton)
                    self.assertTrue(cell.editor.isVisible())
                    cell.editor.selectAll()
                    if key == 'pk':
                        cell.editor.setTime(main.QTime(5,30))
                    else:
                        QTest.keyClicks(cell.editor,text)
                    QTest.keyClick(cell.editor,Qt.Key.Key_Return)
                    QTest.qWait(30)
                    self.assertFalse(cell.editor.isVisible())
                    self.assertIn(text,cell.display.text())
                    save.assert_called()
                cell = w.schedule_table.cellWidget(rows.index('care'),3)
                QTest.mouseClick(cell.display,Qt.MouseButton.LeftButton)
                w.btn_start.setFocus()
                QTest.qWait(30)
                self.assertFalse(cell.editor.isVisible())
            finally:
                w.close()

    def test_eight_resize_edges_cursor_and_left_drag_dispatch(self):
        import main
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        with patch.object(main.MainWindow, '_start_all'), patch.object(main.MainWindow, '_fill_devices'), patch.object(main.MainWindow, '_start_update_check'):
            w = main.MainWindow()
            w.show()
            QTest.qWait(100)
            try:
                expected = [Qt.CursorShape.SizeHorCursor]*2 + [Qt.CursorShape.SizeVerCursor]*2 + [Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeFDiagCursor]
                self.assertEqual(len(w._resize_edges), 8)
                system_window = Mock()
                system_window.startSystemResize.return_value = True
                with patch.object(w, 'windowHandle', return_value=system_window):
                    for handle, cursor in zip(w._resize_edges, expected):
                        self.assertTrue(handle.isVisible())
                        self.assertEqual(handle.cursor().shape(), cursor)
                        self.assertTrue(w.rect().contains(handle.geometry()))
                        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=QPoint(2,2))
                        system_window.startSystemResize.assert_called_with(handle.edges)
                        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=QPoint(2,2))
                    count = system_window.startSystemResize.call_count
                    QTest.mouseClick(w._resize_edges[0], Qt.MouseButton.RightButton)
                    self.assertEqual(system_window.startSystemResize.call_count, count)
                w.showMaximized()
                QTest.qWait(100)
                self.assertTrue(all(not h.isVisible() for h in w._resize_edges))
                w.showNormal()
                QTest.qWait(100)
                self.assertTrue(all(h.isVisible() for h in w._resize_edges))
            finally:
                w.close()

    def test_native_resize_cursor_messages(self):
        import ctypes
        from ctypes.wintypes import MSG
        import main
        with patch.object(main.MainWindow, '_start_all'), patch.object(main.MainWindow, '_fill_devices'), patch.object(main.MainWindow, '_start_update_check'):
            w = main.MainWindow()
            try:
                for hit, shape in [(main.win32con.HTLEFT,main.win32con.IDC_SIZEWE),
                                   (main.win32con.HTTOP,main.win32con.IDC_SIZENS),
                                   (main.win32con.HTTOPLEFT,main.win32con.IDC_SIZENWSE),
                                   (main.win32con.HTTOPRIGHT,main.win32con.IDC_SIZENESW)]:
                    msg = MSG()
                    msg.message = main.win32con.WM_SETCURSOR
                    msg.lParam = hit
                    with patch('main.win32gui.LoadCursor', return_value=123) as load, patch('main.win32gui.SetCursor') as set_cursor:
                        self.assertEqual(w.nativeEvent(b'windows_generic_MSG', ctypes.addressof(msg)), (True,1))
                        load.assert_called_once_with(None,shape)
                        set_cursor.assert_called_once_with(123)
            finally:
                w.close()

    def test_window_resizes_all_pages_and_keeps_small_controls_accessible(self):
        import main
        from PyQt6.QtCore import QPoint
        from PyQt6.QtTest import QTest
        with patch.object(main.MainWindow, '_start_all'), patch.object(main.MainWindow, '_fill_devices'), patch.object(main.MainWindow, '_start_update_check'):
            w = main.MainWindow()
            w.show()
            try:
                self.assertFalse(w.hasHeightForWidth())
                self.assertEqual(w.navigationInterface.width(), 48)
                self.assertEqual(w.titleBar.height(), 32)
                for button in (w.titleBar.minBtn, w.titleBar.maxBtn, w.titleBar.closeBtn):
                    self.assertLessEqual(button.y() + button.height(), w.titleBar.height())
                for width, height in [(1280,820), (800,500), (900,700), (1400,600), (800,500)]:
                    w.resize(width, height)
                    QTest.qWait(100)
                    self.assertEqual((w.width(), w.height()), (width,height))
                    self.assertLessEqual(w._screen_card.width(), w._home_page.width()-400)
                    self.assertTrue(w._size_grip.isVisible())
                    self.assertGreater(w._home_scroll.viewport().width(), 380)
                    self.assertEqual(w._toolbar_card.height(), 56)
                    compact = w._toolbar_card.width() < 900
                    self.assertEqual(w._btn_connect_test.text(), '' if compact else '连接测试')
                    if compact:
                        self.assertEqual(w._btn_connect_test.width(), 36)
                    self.assertFalse(w.navigationInterface.isIndicatorAnimationEnabled())
                    for button in (w.btn_start, w.btn_stop, w.btn_scrcpy, w._btn_connect_test, w._btn_manual_recover, w._runtime_label):
                        self.assertTrue(w._toolbar_card.rect().contains(button.geometry()))
                for i in range(w.stackedWidget.count()):
                    route = w.stackedWidget.widget(i).objectName()
                    nav_button = w.navigationInterface.widget(route)
                    self.assertTrue(nav_button.toolTip())
                    self.assertEqual(nav_button.width(), 40)
                    nav_button.click()
                    QTest.qWait(550)
                    self.assertEqual(w.stackedWidget.currentIndex(), i)
                    self.assertEqual((w.width(),w.height()), (800,500))
                w.stackedWidget.setCurrentIndex(0)
                QTest.qWait(550)
                bar = w._home_scroll.verticalScrollBar()
                self.assertGreater(bar.maximum(), 0)
                bar.setValue(bar.maximum())
                QTest.qWait(50)
                pos = w.log_view.mapTo(w._home_scroll.viewport(), QPoint(0,0))
                self.assertLess(pos.y(), w._home_scroll.viewport().height())
                self.assertGreater(pos.y() + w.log_view.height(), 0)
                self.assertEqual(bar.value(), bar.maximum())
                w.titleBar.maxBtn.click()
                QTest.qWait(100)
                self.assertTrue(w.isMaximized())
                self.assertFalse(w._size_grip.isVisible())
                w.titleBar.maxBtn.click()
                QTest.qWait(100)
                self.assertFalse(w.isMaximized())
            finally:
                w.close()


if __name__ == '__main__':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    unittest.main(verbosity=2)
