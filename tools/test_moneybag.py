"""Money-bag detection and guarded collection, no real device required."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import cv2
import numpy as np
from src.moneybag import MoneyBagCollector, friend_bag_points, SUMMARY, VISITS, confirmed_friend_rows


def element(bounds=(1, 1, 10, 10), text=''):
    return NS(bounds=bounds, attrib={'content-desc': text})


def tree(mapping=None):
    return NS(find_elements=lambda xpath: (mapping or {}).get(xpath, []))


class MoneyBagTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch('src.moneybag.time.sleep').start()
        self.addCleanup(patch.stopall)
        self.dev = Mock()
        self.collector = MoneyBagCollector(self.dev)

    def setup_scroll_device(self, pictures):
        state = {'page': -1}
        rows = [element((917,300,994,350)),element((917,700,994,750))]
        home = tree({'//*[@content-desc="金币胶囊"]':[element()], '//*[@content-desc="friend"]':[element()]})
        listing = tree({VISITS:rows,'//*[@content-desc="金币胶囊"]':[element()]})
        self.dev.hierarchy.side_effect = lambda: home if state['page']<0 else listing
        self.dev.click.side_effect = lambda *args: state.update(page=0)
        self.dev.swipe.side_effect = lambda *args: state.update(page=min(state['page']+1,len(pictures)-1))
        self.dev.d.press.side_effect = lambda *args: state.update(page=-1)
        self.dev.screenshot.side_effect = lambda: pictures[state['page']]
        return rows

    def test_scrolls_until_separator_then_stops(self):
        rows=self.setup_scroll_device([np.full((1000,1080,3),i,dtype=np.uint8) for i in (10,30,50)])
        with patch('src.moneybag.recommendation_boundary',side_effect=[None,None,500]),patch('src.moneybag.friend_bag_points',return_value=[]) as detect:
            self.assertEqual(self.collector.run(),0)
            self.assertEqual(self.dev.swipe.call_count,2)
            self.assertEqual(detect.call_args.args[1],[rows[0].bounds])
        self.dev.d.press.assert_called_once_with('back')

    def test_first_page_boundary_never_scrolls(self):
        self.setup_scroll_device([np.zeros((1000,1080,3),dtype=np.uint8)])
        with patch('src.moneybag.recommendation_boundary',return_value=500),patch('src.moneybag.friend_bag_points',return_value=[]):
            self.collector.run()
        self.dev.swipe.assert_not_called()

    def test_stuck_list_stops_without_looping(self):
        self.setup_scroll_device([np.zeros((1000,1080,3),dtype=np.uint8)])
        with patch('src.moneybag.recommendation_boundary',return_value=None),patch('src.moneybag.friend_bag_points',return_value=[]):
            self.collector.run()
        self.assertEqual(self.dev.swipe.call_count,1)

    def test_recommended_badge_position_excluded_without_separator(self):
        tile=cv2.cvtColor(cv2.imread('resources/moneybag-friend.png'),cv2.COLOR_BGR2RGB)
        screen=np.full((2340,1080,3),25,dtype=np.uint8)
        screen[967:1033,690:756]=tile
        self.assertEqual(friend_bag_points(screen,[(917,974,994,1026)]),[])

    def test_recommendations_excluded_at_all_sizes(self):
        for width in (720,1080,1440):
            screen = np.zeros((width*2,width,3), dtype=np.uint8)
            rows = [element((0,100,100,200)),element((0,500,100,600)),element((0,700,100,800))]
            with patch('src.ocr.ocr_texts', return_value=[('他们都在玩',20,550,.99)]):
                self.assertEqual(confirmed_friend_rows(screen,rows),rows[:1])

    def test_missing_or_uncertain_boundary_rejects_every_row(self):
        screen = np.zeros((2340,1080,3), dtype=np.uint8)
        for text in ([],[('他们都在玩',20,550,.6)],[('其他文字',20,550,.99)]):
            with patch('src.ocr.ocr_texts', return_value=text):
                self.assertEqual(confirmed_friend_rows(screen,[element()]),[])

    def test_refresh_does_not_reuse_old_boundary(self):
        screen = np.zeros((2340,1080,3), dtype=np.uint8)
        with patch('src.ocr.ocr_texts', side_effect=[[('他们都在玩',20,550,.99)],[]]):
            self.assertEqual(len(confirmed_friend_rows(screen,[element()])),1)
            self.assertEqual(confirmed_friend_rows(screen,[element()]),[])

    def test_image_scaling_and_context(self):
        tile = cv2.cvtColor(cv2.imread('resources/moneybag-friend.png'), cv2.COLOR_BGR2RGB)
        screen = np.full((2340,1080,3), 25, dtype=np.uint8)
        screen[967:1033,777:843] = tile
        for width in (720,1080,1440):
            scale = width/1080
            image = cv2.resize(screen,(width,round(2340*scale)))
            bounds = [tuple(round(v*scale) for v in (917,974,994,1026))]
            points = friend_bag_points(image,bounds)
            self.assertEqual(len(points),1)
            self.assertLess(abs(points[0][0]-810*scale),3)
            self.assertEqual(friend_bag_points(image,[]),[])

    def test_pink_similar_shape_rejected(self):
        tile = cv2.imread('resources/moneybag-friend.png')
        hsv = cv2.cvtColor(tile,cv2.COLOR_BGR2HSV)
        hsv[:,:,0] = 165
        pink = cv2.cvtColor(hsv,cv2.COLOR_HSV2RGB)
        screen = np.full((2340,1080,3),25,dtype=np.uint8)
        screen[967:1033,777:843] = pink
        self.assertEqual(friend_bag_points(screen,[(917,974,994,1026)]),[])

    def test_outside_badge_region_rejected(self):
        tile = cv2.cvtColor(cv2.imread('resources/moneybag-friend.png'),cv2.COLOR_BGR2RGB)
        screen = np.zeros((2340,1080,3),dtype=np.uint8)
        screen[967:1033,77:143] = tile
        self.assertEqual(friend_bag_points(screen,[(917,974,994,1026)]),[])

    def test_result_closes_small_x_only(self):
        self.dev.hierarchy.side_effect = [tree({SUMMARY:[element(text='已领1个')], '//*[@content-desc="关闭"]':[element((0,0,1080,2340)),element((499,1745,582,1828))]}),tree()]
        self.assertEqual(self.collector.collect_at(237,1445),'已领1个')
        self.assertEqual(self.dev.click.call_args_list[1].args,(540,1786))

    def test_ambiguous_close_does_not_click(self):
        self.dev.hierarchy.return_value = tree({SUMMARY:[element()], '//*[@content-desc="关闭"]':[element(),element()]})
        with self.assertRaises(RuntimeError): self.collector.collect_at(1,2)
        self.dev.click.assert_called_once_with(1,2)

    def test_missing_result_no_repeat(self):
        self.dev.hierarchy.return_value = tree()
        with self.assertRaises(RuntimeError): self.collector.collect_at(1,2)
        self.dev.click.assert_called_once_with(1,2)

    def test_friend_no_amount_result(self):
        self.dev.hierarchy.return_value = tree({'//*[@content-desc="回家"]':[element()]})
        self.assertEqual(self.collector.collect_at(1,2,friend=True),'好友成长福袋已点击')
        self.dev.click.assert_called_once()

    def test_existing_modal_no_interaction(self):
        self.dev.hierarchy.return_value = tree({'//*[@content-desc="金币胶囊"]':[element()], '//*[@content-desc="关闭"]':[element()]})
        self.assertEqual(self.collector.run(),0)
        self.dev.click.assert_not_called()

    def test_wrong_page_no_interaction(self):
        self.dev.hierarchy.return_value = tree()
        self.assertEqual(self.collector.run(),0)
        self.dev.click.assert_not_called()

    def test_ambiguous_friend_row_no_interaction(self):
        with self.assertRaises(RuntimeError): self.collector._visit_bag(5,[element(),element()])
        self.dev.click.assert_not_called()

    def test_cooldown_and_failure(self):
        from scenarios.runner import Runner
        runner = Runner.__new__(Runner)
        runner._moneybag_next = 0
        runner.school = NS(dev=self.dev)
        with patch('src.moneybag.MoneyBagCollector.run',side_effect=RuntimeError('offline')) as run:
            self.assertFalse(runner._try_moneybags())
            self.assertFalse(runner._try_moneybags())
            run.assert_called_once()

    def test_pending_settlement_has_priority(self):
        from scenarios.runner import TaskQueueRunner
        runner = TaskQueueRunner.__new__(TaskQueueRunner)
        runner.employed_due = Mock(return_value=False)
        runner._pending_finish_first = Mock(return_value='finish')
        runner._try_moneybags = Mock()
        self.assertTrue(runner._run_first_due({},[]))
        runner._try_moneybags.assert_not_called()

if __name__ == '__main__':
    unittest.main()
