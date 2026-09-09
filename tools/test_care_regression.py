"""护理定位与状态栏污染的离线回归测试，不连接设备、不写进度。"""
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.locators import LOCATORS
from scenarios.care import CareScenario, parse_status


class CareRegression(unittest.TestCase):
    def test_panel_toggle_is_idempotent(self):
        care = CareScenario.__new__(CareScenario)
        for bounds, visible in [((204,543,740,597), True),
                                ((204,543,740,229), False),
                                ((204,543,740,543), False), (None, False)]:
            for requested in (True, False):
                calls = []
                care.dev = SimpleNamespace(find_xpath_bounds=lambda *a, **k: bounds)
                care.toggle_status = lambda source: calls.append(source)
                care.set_status_expanded(requested, 'snapshot')
                self.assertEqual(len(calls), int(visible != requested))

    def test_items_do_not_match_friend_list(self):
        root = etree.fromstring('''<hierarchy>
          <androidx.recyclerview.widget.RecyclerView>
            <android.widget.FrameLayout><android.widget.FrameLayout>
              <node content-desc="好友 测试好友"/>
            </android.widget.FrameLayout></android.widget.FrameLayout>
          </androidx.recyclerview.widget.RecyclerView>
          <node content-desc="饼干"/><node content-desc="香皂片，剩余10"/>
          <node content-desc="饼干，5金币"/><node content-desc="香皂片，2金币"/>
        </hierarchy>'''.encode())
        for key, expected in [('feed_10', '饼干'), ('shower_10', '香皂片，剩余10')]:
            path = LOCATORS[key]['xpath'][0]
            hits = root.xpath(path)
            self.assertEqual([n.get('content-desc') for n in hits], [expected])
            root.remove(hits[0])
            self.assertEqual(root.xpath(path), [])

    def test_names_ignore_status_bar_and_side_badge(self):
        rows = [('19:326', 171, 45, 1), ('0.63K/s97%', 821, 44, 1),
                ('测试账号', 425, 137, 1), ('测试宠物', 377, 193, 1),
                ('15天', 809, 203, 1), ('体力 83', 335, 281, 1),
                ('清洁79', 336, 386, 1), ('心情100', 345, 489, 1)]
        for factor in (1, 2/3):
            scaled = [(t, x*factor, y*factor, s) for t, x, y, s in rows]
            result = parse_status(scaled, 1.5*factor)
            self.assertEqual(result['账号名称'], '测试账号')
            self.assertEqual(result['宠物名称'], '测试宠物')
            self.assertEqual(result['清洁'], 79)
        missing = rows[:2] + rows[4:]
        self.assertNotIn('宠物名称', parse_status(missing, 1.5))


if __name__ == '__main__':
    unittest.main()
