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



class SelectionRetryRegression(unittest.TestCase):
    def scenario(self, frames):
        from src.scenario import DeviceScenario
        s = DeviceScenario.__new__(DeviceScenario)
        s.dev = SimpleNamespace(hierarchy=Mock(side_effect=frames), drag=Mock())
        s.see = Mock(side_effect=lambda name, source=None: source.get(name) if source else None)
        return s

    def test_new_school_layout_has_course_container(self):
        from uiautomator2.xpath import PageSource
        from src.locators import SELECT_BOX_XPATH
        course = ''.join(chr(i) for i in (21435, 19978, 35838))
        xml = f'''<hierarchy><node class="androidx.recyclerview.widget.RecyclerView"
            bounds="[65,1062][1015,1950]"><node class="android.widget.FrameLayout"
            bounds="[65,1062][1015,1943]"><node class="android.widget.FrameLayout"
            bounds="[65,1062][1015,1445]"><node class="android.widget.FrameLayout"
            bounds="[65,1084][1015,1445]"><node class="androidx.recyclerview.widget.RecyclerView"
            bounds="[65,1084][1015,1445]"><node class="android.widget.FrameLayout"
            bounds="[65,1084][1015,1445]" /></node></node></node></node></node>
            <node class="android.widget.Button" content-desc="{course}"
            bounds="[108,1983][972,2113]" /></hierarchy>'''
        page = PageSource.parse(xml)
        self.assertFalse(page.find_elements(SELECT_BOX_XPATH))
        hits = page.find_elements(LOCATORS['select_box_container']['xpath'][1])
        self.assertEqual([hit.bounds for hit in hits], [(65, 1084, 1015, 1445)])

    def test_work_panel_locator_matches_current_layout(self):
        from uiautomator2.xpath import PageSource
        work = ''.join(chr(i) for i in (21435, 25171, 24037))
        xml = f'''<hierarchy><node class="androidx.recyclerview.widget.RecyclerView"
            bounds="[65,954][1015,1951]"><node class="android.widget.FrameLayout"
            bounds="[65,954][1015,1944]"><node class="android.widget.FrameLayout"
            bounds="[65,954][1015,1350]"><node class="android.widget.FrameLayout"
            bounds="[65,976][1015,1337]"><node class="androidx.recyclerview.widget.RecyclerView"
            bounds="[65,976][1015,1337]"><node class="android.widget.FrameLayout"
            bounds="[65,976][1015,1337]" /></node></node></node></node></node>
            <node class="android.widget.Button" content-desc="{work}"
            bounds="[108,1983][972,2113]" /></hierarchy>'''
        page = PageSource.parse(xml)
        self.assertFalse(page.find_elements(LOCATORS['select_box_container']['xpath'][1]))
        hits = page.find_elements(LOCATORS['select_box_container']['xpath'][2])
        self.assertEqual([hit.bounds for hit in hits], [(65, 976, 1015, 1337)])

    @patch('src.scenario.log')
    @patch('src.scenario.time.sleep')
    def test_switching_panels_uses_new_bounds(self, *_):
        from src.locators import _bounds_cache
        from src.scenario import DeviceScenario
        old_cache = _bounds_cache.copy()
        _bounds_cache.clear()
        try:
            panels = [{'kind': 'school'}, {'kind': 'work'}]
            dev = SimpleNamespace(hierarchy=Mock(side_effect=panels), drag=Mock())
            def bounds(path, source):
                if source['kind'] == 'school' and '去上课' in path:
                    return (65, 1084, 1015, 1445)
                if source['kind'] == 'work' and '去打工' in path:
                    return (65, 976, 1015, 1337)
                return None
            dev.find_xpath_bounds = Mock(side_effect=bounds)
            s = DeviceScenario.__new__(DeviceScenario)
            s.dev = dev
            s.reset_select_boxes(drags=1)
            s.reset_select_boxes(drags=1)
            self.assertEqual(dev.drag.call_args_list[0].args, (255, 1264, 920, 1264))
            self.assertEqual(dev.drag.call_args_list[1].args, (255, 1156, 920, 1156))
        finally:
            _bounds_cache.clear()
            _bounds_cache.update(old_cache)

    @patch('src.scenario.log')
    @patch('src.scenario.time.sleep')
    def test_delayed_cards_refresh_and_share_snapshot(self, *_):
        cards = {'select_box_1': (100, 200, 1), 'select_box_3': (500, 200, 1)}
        s = self.scenario([{}, cards])
        s.reset_select_boxes(drags=1)
        self.assertEqual(s.dev.hierarchy.call_count, 2)
        s.dev.drag.assert_called_once_with(100, 200, 500, 200)
        self.assertIs(s.see.call_args_list[-1].kwargs['source'], cards)
        self.assertIs(s.see.call_args_list[-2].kwargs['source'], cards)

    @patch('src.scenario.log')
    @patch('src.scenario.time.sleep')
    def test_missing_cards_never_drag(self, *_):
        s = self.scenario([{}, {}, {}])
        with self.assertRaisesRegex(RuntimeError, '未定位到选择框'):
            s.reset_select_boxes()
        self.assertEqual(s.dev.hierarchy.call_count, 3)
        s.dev.drag.assert_not_called()

    @patch('src.scenario.log')
    @patch('src.scenario.time.sleep')
    def test_supplied_snapshot_only_used_for_first_attempt(self, *_):
        cards = {'select_box_1': (100, 200, 1), 'select_box_3': (500, 200, 1)}
        s = self.scenario([cards])
        s.reset_select_boxes(drags=1, source={})
        s.dev.hierarchy.assert_called_once()
        s.dev.drag.assert_called_once()

if __name__ == '__main__':
    unittest.main(verbosity=2)
