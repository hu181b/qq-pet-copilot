"""Recommended-user actions remain independent of the growth-bag-only gate."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace as NS
import numpy as np
from scenarios.visit import VisitScenario
from scenarios.friend_care import FriendCareScenario
from src.moneybag import confirmed_friend_rows

class RecommendationScopeTests(unittest.TestCase):
    def setUp(self):
        self.gate=patch('src.moneybag.confirmed_friend_rows',side_effect=AssertionError('Other actions must not use bag gate')).start()
        patch('scenarios.visit.time.sleep').start()
        patch('scenarios.friend_care.time.sleep').start()
        self.addCleanup(patch.stopall)

    def test_shared_list_keeps_recommended_entry_and_can_switch(self):
        s=VisitScenario.__new__(VisitScenario)
        s.dev=Mock();s.click=Mock()
        s.dev.d.xpath.return_value.all.return_value=[NS(bounds=(10,10,60,60),attrib={'content-desc':'好友 真实好友'}),NS(bounds=(10,100,60,150),attrib={'content-desc':'好友 推荐用户'})]
        s._friends=['好友 真实好友'];s._friend_index=0
        self.assertTrue(s.next_friend())
        s.click.assert_called_once_with(35,125)
        self.gate.assert_not_called()

    def test_step_still_clicks_on_recommended_home(self):
        s=VisitScenario.__new__(VisitScenario);s.dev=Mock();s.click=Mock()
        s.see=Mock(side_effect=lambda name,**kw: (90,120) if name=='visit_step' else None)
        self.assertEqual(s.step_once(),'stepped')
        s.click.assert_called_once_with(90,120)
        self.gate.assert_not_called()

    def test_feeding_and_bathing_still_dispatch(self):
        for energy,clean,feed,shower in [(50,100,1,0),(100,50,0,1),(50,50,1,1)]:
            with self.subTest(energy=energy,clean=clean),patch('scenarios.friend_care._FriendCare') as cls:
                s=FriendCareScenario.__new__(FriendCareScenario);s.dev=Mock();s.method='ocr检测'
                cls.return_value.read_status_ready.return_value={'体力':energy,'清洁':clean}
                self.assertTrue(s.care_friend())
                self.assertEqual(cls.return_value.feed.call_count,feed)
                self.assertEqual(cls.return_value.shower.call_count,shower)
                self.gate.assert_not_called()

    def test_named_recommended_target_still_selectable(self):
        s=FriendCareScenario.__new__(FriendCareScenario)
        s._friends=[];s._friend_items=Mock(return_value=[('好友 推荐用户',40,80)]);s.click=Mock()
        self.assertTrue(s.switch_to_friend('推荐用户'))
        s.click.assert_called_once_with(40,80)
        self.gate.assert_not_called()

    def test_one_click_care_still_dispatches(self):
        s=FriendCareScenario.__new__(FriendCareScenario);s.method='一键护理';s.click=Mock()
        s.see=Mock(side_effect=lambda name:(20,30) if name=='one_click_care' else (40,50))
        self.assertTrue(s.care_friend())
        self.assertEqual([c.args for c in s.click.call_args_list],[(20,30),(40,50)])
        self.gate.assert_not_called()

    def test_bag_filter_does_not_mutate_shared_rows(self):
        rows=[NS(bounds=(1,100,50,150)),NS(bounds=(1,700,50,750))]
        original=list(rows)
        with patch('src.ocr.ocr_texts',return_value=[('他们都在玩',10,500,.99)]):
            self.assertEqual(confirmed_friend_rows(np.zeros((900,1080,3),dtype=np.uint8),rows),rows[:1])
        self.assertEqual(rows,original)

if __name__=='__main__':unittest.main()
