"""Profile dialog interaction checks; no phone or real configuration writes."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import onnxruntime
import main
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog


class ProfileDialogRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = main.QApplication.instance() or main.QApplication([])

    def check_button(self, action):
        patches = [patch.object(main.MainWindow, name) for name in
                   ('_start_all', '_fill_devices', '_start_update_check')]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        parent = main.MainWindow()
        # Embedding scrcpy makes the preview and its ancestors native windows.
        parent.findChild(main.ScrcpyContainer).winId()
        parent.resize(800, 500)
        parent.show()
        self.addCleanup(parent.close)
        parent._runner_proc = NS(poll=lambda: None)
        self.addCleanup(setattr, parent, '_runner_proc', None)
        parent._profiles = Mock()
        parent.profile_combo.addItem('测试配置', userData='a' * 32)
        parent.profile_combo.setCurrentIndex(parent.profile_combo.count() - 1)
        state = []
        def click():
            boxes = parent.findChildren(main.QMessageBox)
            self.assertEqual(len(boxes), 1)
            box = boxes[0]
            button = box.button(main.QMessageBox.StandardButton.Ok)
            state.append(('click', button.isEnabled(), button.isVisible()))
            pos = button.mapToGlobal(button.rect().center())
            target = self.app.widgetAt(pos)
            state.append(('target', type(target).__name__, target is button))
            state.append(('independent_window', box.isWindow()))
            if self.app.platformName() == 'windows':
                style = main.win32gui.GetWindowLong(int(box.winId()), main.win32con.GWL_STYLE)
                state.append(('native_child', bool(style & main.win32con.WS_CHILD)))
            if action == 'mouse':
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            else:
                QTest.keyClick(box, action)
        def timeout():
            state.append('timeout')
            for box in parent.findChildren(main.QMessageBox):
                QDialog.done(box, 0)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(timeout)
        timer.start(1800)
        QTimer.singleShot(300, click)
        parent._switch_profile()
        timer.stop()
        self.assertNotIn('timeout', state, state)
        self.assertEqual(state[1][-1], True, state)
        self.assertEqual(state[2], ('independent_window', True), state)
        if self.app.platformName() == 'windows':
            self.assertEqual(state[3], ('native_child', False), state)
        parent._profiles.select.assert_not_called()
        self.assertIsNone(parent._next_profile)
        self.assertIsNone(parent._runner_proc.poll())
        self.assertTrue(parent.isVisible())
        self.app.processEvents()

    def test_ok_dismisses(self):
        self.check_button('mouse')

    def test_escape_dismisses(self):
        self.check_button(Qt.Key.Key_Escape)

    def test_enter_dismisses(self):
        self.check_button(Qt.Key.Key_Return)


if __name__ == '__main__':
    unittest.main(verbosity=2)
