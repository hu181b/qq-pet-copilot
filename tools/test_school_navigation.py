"""学园导航回归：不连接手机，不修改配置/进度。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lxml import etree
from src.locators import LOCATORS
from scenarios.school import SchoolScenario


class SchoolNavigationRegression(unittest.TestCase):
    def scenario(self, frames):
        s = SchoolScenario.__new__(SchoolScenario)
        s._graduated_once = False
        s.dev = SimpleNamespace(hierarchy=Mock(side_effect=frames))
        s.leave_home = Mock()
        s.wait_busy_end = Mock(return_value=None)
        s.handle_low_stat_dialog = Mock()
        s._recheck_busy_after_nav = Mock(return_value=None)
        s.click = Mock()
        s.see = lambda name, screen=None, source=None: (100, 200, 1) if name in source else None
        return s

    def test_entry_does_not_match_academy_buildings_or_title(self):
        root = etree.fromstring('''<hierarchy><node content-desc="map_blank">
        <android.widget.FrameLayout/><android.widget.FrameLayout>
        <android.widget.FrameLayout content-desc="academy_2"/>
        </android.widget.FrameLayout></node><node content-desc="宠物学园"/>
        </hierarchy>'''.encode())
        self.assertFalse(root.xpath(LOCATORS['school']['xpath'][0]))
        self.assertTrue(root.xpath(LOCATORS['school_map']['xpath'][0]))
        etree.SubElement(root, 'node', {'content-desc': 'study'})
        self.assertEqual(len(root.xpath(LOCATORS['school']['xpath'][0])), 1)

    @patch('scenarios.school.log')
    @patch('scenarios.school.time.sleep')
    def test_delayed_panel_never_clicks_underlying_map(self, *_):
        s = self.scenario([{'school'}, {'school', 'school_map'},
                           {'school', 'school_map'}, {'school_start', 'school_map'}])
        self.assertIsNone(s.goto_school())
        s.click.assert_called_once_with(100, 200)
        self.assertEqual(s.dev.hierarchy.call_count, 4)

    @patch('scenarios.school.log')
    @patch('scenarios.school.time.sleep')
    def test_disappeared_entry_waits_for_confirmed_course(self, *_):
        s = self.scenario([{'school'}, set(), {'school_start'}])
        self.assertIsNone(s.goto_school())
        self.assertEqual(s.dev.hierarchy.call_count, 3)

    @patch('scenarios.school.log')
    @patch('scenarios.school.time.sleep')
    @patch('scenarios.school.NAV_TIMEOUT', 3)
    def test_missing_panel_times_out_without_starting_course(self, *_):
        s = self.scenario([{'school'}, {'school_map'}, {'school_map'}])
        with self.assertRaisesRegex(RuntimeError, '仍未出现 school_start'):
            s.goto_school()
        s.click.assert_called_once()
        s._recheck_busy_after_nav.assert_called_once()

    @patch('scenarios.school.log')
    @patch('scenarios.school.time.sleep')
    def test_graduation_can_reenter_current_course(self, *_):
        s = self.scenario([{'school_graduated'}, {'school'}, {'school_start'}])
        s._close_graduation = Mock()
        self.assertEqual(s.goto_school(), 'graduated')
        s._close_graduation.assert_called_once()
        self.assertIsNone(s.goto_school())
        self.assertFalse(s._graduated_once)

    @patch('scenarios.school.log')
    def test_repeated_certificate_is_bounded(self, *_):
        s = self.scenario([{'school_graduated'}])
        s._graduated_once = True
        with self.assertRaisesRegex(RuntimeError, '仍出现毕业标志'):
            s.goto_school()
        s.click.assert_not_called()

    def test_busy_activity_is_preserved(self):
        s = self.scenario([])
        s.wait_busy_end.return_value = 'work'
        s.ensure_main_page = Mock()
        self.assertEqual(s.goto_school(), 'work')
        s.click.assert_not_called()
        s.dev.hierarchy.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
