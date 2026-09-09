"""三轮验收共用的离线功能测试。设备输入、外部通知均为桩；进度写临时目录。"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import onnxruntime  # 与打包 runtime hook 一致：必须在 Qt 前初始化 ORT。
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.test_care_regression import CareRegression
from src.ocr import ocr_fullscreen, parse_panel_location, parse_employed_remaining
from src.settings import WORK_LOCATIONS, validate_field
from src import progress_store as store
from src.config import load_config
from scenarios.work import WorkScenario
from scenarios.school import SchoolScenario
from scenarios.adventure import AdventureScenario
from scenarios.visit import VisitScenario
from scenarios.pk import PKScenario, PK_ROUND_CAP
from scenarios.friend_care import FriendCareScenario, parse_time_range, in_time_range
from scenarios.hire_friend import FriendHireScenario
from scenarios.employed import EmployedScenario
from scenarios.runner import Runner, TaskQueueRunner, _QueueTask, ScenarioFailed
from src.scenario import TaskDeferred

WIDTH = int(os.environ.get('TEST_WIDTH', '1080'))


def work_rows(location='云朵梦舍'):
    rows = [('力量784',260,284,1), ('智力883',566,285,1), ('魅力3209',872,282,1),
            ('闪耀星屋',906,344,1), (location,466,570,1), ('规则说明',880,890,1),
            ('雇佣有额外加成',830,1450,1), ('去打工',540,2040,1)]
    return [(t,x*WIDTH/1080,y*WIDTH/1080,s) for t,x,y,s in rows]


class FullRegression(unittest.TestCase):
    def test_packaged_runtime_uses_shared_adb_and_limited_threads(self):
        import runpy
        from adbutils._utils import adb_path
        with tempfile.TemporaryDirectory() as folder:
            adb=Path(folder)/'resources/scrcpy-win64/adb.exe'
            adb.parent.mkdir(parents=True);adb.touch()
            with patch.dict(os.environ,{},clear=True), patch.object(sys,'frozen',True,create=True), patch.object(sys,'_MEIPASS',folder,create=True):
                runpy.run_path('tools/pyi_rth_preload_onnxruntime.py')
                self.assertEqual(adb_path(),str(adb))
                self.assertEqual(os.environ['OPENBLAS_NUM_THREADS'],'1')

    def test_config_cache_refresh_and_mutation_isolation(self):
        from src import config
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.yaml'
            path.write_text('work:\n  duration: 10分钟\n',encoding='utf-8')
            config._read_config_cached.cache_clear()
            first=load_config(path);first.work.duration='2小时'
            self.assertEqual(load_config(path).work.duration,'10分钟')
            self.assertEqual(config._read_config_cached.cache_info().hits,1)
            replacement=path.with_suffix('.tmp')
            replacement.write_text('work:\n  duration: 45分钟\n',encoding='utf-8');replacement.replace(path)
            self.assertEqual(load_config(path).work.duration,'45分钟')

    def test_queue_unchanged_write_throttling_and_recreation(self):
        from src import queue_status as queue
        with tempfile.TemporaryDirectory() as folder, patch.object(queue,'_last_write',None):
            path=Path(folder)/'queue.json'
            with patch.object(queue,'QUEUE_STATUS_FILE',path), patch.object(queue.time,'monotonic',return_value=1):
                queue.save_queue_status({'current':'work','updated':'a'})
                queue.save_queue_status({'current':'work','updated':'b'})
                self.assertEqual(queue.load_queue_status()['updated'],'a')
                queue.save_queue_status({'current':'care','updated':'c'})
                self.assertEqual(queue.load_queue_status()['current'],'care')
                path.unlink()
                queue.save_queue_status({'current':'care','updated':'d'})
                self.assertTrue(path.is_file())
            with patch.object(queue,'QUEUE_STATUS_FILE',path), patch.object(queue.time,'monotonic',return_value=40):
                queue.save_queue_status({'current':'care','updated':'e'})
                self.assertEqual(queue.load_queue_status()['updated'],'e')

    def test_native_embedded_window_counts_as_foreground(self):
        import main
        window=NS(winId=lambda:100,isActiveWindow=lambda:False)
        with patch('main.win32gui.GetForegroundWindow',return_value=200), patch('main.win32gui.GetAncestor',side_effect=lambda hwnd,flag:100):
            self.assertTrue(main.window_is_foreground(window))
        with patch('main.win32gui.GetForegroundWindow',return_value=300), patch('main.win32gui.GetAncestor',side_effect=lambda hwnd,flag:hwnd):
            self.assertFalse(main.window_is_foreground(window))

    def test_legacy_config_ignores_removed_emulator_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.yaml'
            path.write_text('emulator:\n  type: MuMuPlayer12\nrecover:\n  method: 重启游戏\n  emulator_restart_cmd: obsolete\n',encoding='utf-8')
            cfg=load_config(path)
            self.assertFalse(hasattr(cfg,'emulator'))
            self.assertFalse(hasattr(cfg.recover,'emulator_restart_cmd'))
            self.assertEqual(cfg.recover.method,'重启游戏')

    def test_ocr_uses_only_one_cpu_thread_and_no_gpu(self):
        from src.ocr import get_engine
        engine=get_engine()
        for model in (engine.text_det,engine.text_cls,engine.text_rec):
            self.assertEqual(model.session.session.get_providers(),['CPUExecutionProvider'])
            options=model.session.session.get_session_options()
            self.assertEqual(options.intra_op_num_threads,1)
            self.assertEqual(options.inter_op_num_threads,1)

    def test_phone_recover_official_scheme(self):
        from src import recover
        adb=Mock(); adb.getprop.return_value='phone'; dev=Mock()
        with patch.object(recover,'_connect_u2',return_value=dev), patch.object(recover,'_wait_qq_settle'), patch.object(recover,'_open_pet_via_scheme') as scheme, patch.object(recover,'_wait_main_page',return_value=True):
            self.assertIs(recover.reenter_pet(adb,'重启游戏'),dev)
            adb.force_stop_app.assert_called_once()
            adb.reboot_and_wait.assert_not_called()
            scheme.assert_called_once_with(adb.adb,adb.serial)

    def test_gui_starts_phone_runner_with_lower_priority(self):
        import main
        window=NS(_runner_proc=None,_read_runner_logs=Mock())
        with patch('main.subprocess.Popen') as popen, patch('main.threading.Thread'):
            main.MainWindow.start_runner(window)
            self.assertTrue(popen.call_args.kwargs['creationflags'] & main.subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            self.assertFalse(any('emulator' in arg for arg in popen.call_args.args[0]))

    def test_background_mirror_pauses_once_and_resumes(self):
        import main
        window=NS(btn_scrcpy=Mock(), isActiveWindow=Mock(return_value=False),
                  isMinimized=Mock(return_value=False), _background_mirror_paused=False,
                  _disable_scrcpy=Mock(), _enable_scrcpy=Mock())
        window.btn_scrcpy.isChecked.return_value=True
        main.MainWindow._check_scrcpy(window)
        main.MainWindow._check_scrcpy(window)
        window._disable_scrcpy.assert_called_once()
        window._enable_scrcpy.assert_not_called()
        window.isActiveWindow.return_value=True
        main.MainWindow._check_scrcpy(window)
        window._enable_scrcpy.assert_called_once()
        self.assertFalse(window._background_mirror_paused)

    def test_background_manual_mirror_off_stays_off(self):
        import main
        window=NS(btn_scrcpy=Mock(), _background_mirror_paused=True,
                  _disable_scrcpy=Mock(), _enable_scrcpy=Mock())
        window.btn_scrcpy.isChecked.return_value=False
        main.MainWindow._check_scrcpy(window)
        window._enable_scrcpy.assert_not_called()

    def test_background_skips_stats_file_reads(self):
        import main
        window=NS(isActiveWindow=Mock(return_value=False))
        with patch('main.load_config',side_effect=AssertionError('后台不读配置')):
            main.MainWindow._refresh_stats(window)

    def test_tracked_mirror_stop_does_not_scan_other_processes(self):
        import main
        proc=Mock(); proc.poll.return_value=None
        with patch('main._kill_scrcpy_by_marker',side_effect=AssertionError('不应扫描')):
            main.kill_our_scrcpy(proc)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=2)

    def test_hire_work_records_actual_duration_for_settlement(self):
        from src import progress
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'work.json'
            for duration,seconds in [('10分钟',600),('45分钟',2700),('2小时',7200)]:
                for deferred in (False,True):
                    with self.subTest(duration=duration,deferred=deferred):
                        target.unlink(missing_ok=True)
                        sc=FriendHireScenario.__new__(FriendHireScenario)
                        sc.dev=Mock(); sc.defer_wait=deferred
                        for method in ('_enter_work_panel','_select_job','defer_busy_end','ensure_main_page'):
                            setattr(sc,method,Mock())
                        work=Mock(duration=duration)
                        with patch('scenarios.hire_friend.WorkScenario',return_value=work), patch.object(progress,'WORK_PROGRESS_FILE',target):
                            sc._hire_and_work()
                            self.assertEqual(progress.get_current_work_duration(),duration)
                            self.assertEqual(progress.record_work_finish(),seconds)

    def test_work_eight_locations_with_neighbour(self):
        for location in WORK_LOCATIONS:
            with self.subTest(location=location):
                self.assertEqual(parse_panel_location(work_rows(location), WIDTH), location)

    def test_work_map_without_panel_rejected(self):
        self.assertIsNone(parse_panel_location(work_rows()[:5], WIDTH))

    def test_work_missing_or_ambiguous_name_rejected(self):
        rows=work_rows('未知建筑')
        self.assertIsNone(parse_panel_location(rows, WIDTH))
        rows=work_rows()+[('彩虹画室',610*WIDTH/1080,570*WIDTH/1080,1)]
        self.assertIsNone(parse_panel_location(rows, WIDTH))

    def test_work_real_failure_screenshots(self):
        names = ('alert_20260908_222232.png','error_retry1_20260908_222005.png',
                 'error_retry1_20260908_222448.png')
        root = Path(__file__).resolve().parents[2]/'runs'
        missing = [name for name in names if not (root/name).is_file()]
        if missing:
            self.skipTest('历史异常截图缺失，不能验证原现场: ' + ', '.join(missing))
        for name in names:
            path=root/name
            with self.subTest(image=name):
                im=Image.open(path).convert('RGB')
                im=im.resize((WIDTH, round(im.height*WIDTH/im.width)))
                self.assertEqual(parse_panel_location(ocr_fullscreen(np.array(im)), WIDTH),'云朵梦舍')

    def test_work_retained_navigation_screenshot(self):
        # 固定素材保存在测试目录，避免依赖可清理的运行截图。
        path = Path(__file__).parent/'fixtures'/'work-cloud-panel.png'
        if not path.is_file():
            self.skipTest('公开源码不附带维护者的游戏截图；本地保留素材时执行此项')
        im = Image.open(path).convert('RGB')
        im = im.resize((WIDTH, round(im.height*WIDTH/im.width)))
        self.assertEqual(parse_panel_location(ocr_fullscreen(np.array(im)), WIDTH), '云朵梦舍')

    def test_work_correct_panel_does_not_back_or_click(self):
        work=WorkScenario.__new__(WorkScenario)
        work.location='云朵梦舍'
        work.screen=lambda:np.zeros((WIDTH*2,WIDTH,3),dtype=np.uint8)
        work.go_back=Mock(side_effect=AssertionError('正确面板不应返回'))
        work.click=Mock(side_effect=AssertionError('正确面板不应重新点地图'))
        with patch('scenarios.work.ocr_fullscreen',return_value=work_rows()), patch('scenarios.work.time.sleep'):
            work.select_place()

    def test_work_wrong_place_not_accepted(self):
        work=WorkScenario.__new__(WorkScenario)
        work.location='彩虹画室'
        work.screen=lambda:np.zeros((WIDTH*2,WIDTH,3),dtype=np.uint8)
        with patch('scenarios.work.ocr_fullscreen',return_value=work_rows()):
            self.assertFalse(work.is_correct_place_panel())

    def test_school_course_orders(self):
        sc=SchoolScenario.__new__(SchoolScenario)
        sc.screen=lambda:np.zeros((1280,720,3),dtype=np.uint8)
        for stage, order in [('初级学园',('力量','智力','魅力')),
                             ('中级学园',('力量','智力','魅力')),
                             ('高级学园',('魅力','力量','智力')),
                             ('进修学院',('力量','魅力','智力'))]:
            for i, attribute in enumerate(order,1):
                sc.attribute=attribute
                with self.subTest(stage=stage,attribute=attribute), patch('scenarios.school.ocr_texts',return_value=[(stage+'5年级',360,100,1)]), patch('scenarios.school.set_current_school'):
                    self.assertEqual(sc.resolve_course_box(),f'select_box_{i}')

    def test_adventure_normal_and_bad_weather_paths(self):
        for recall in (False,True):
            sc=AdventureScenario.__new__(AdventureScenario)
            sc.skip_bad_weather=True; sc.defer_wait=True
            sc.click_until_gone_or_see=Mock(); sc.recall_bad_weather=Mock(return_value=recall)
            sc.defer_busy_end=Mock(); sc.ensure_main_page=Mock()
            with patch('scenarios.adventure.count_cross') as count:
                self.assertEqual(sc.do_adventure(),recall)
                self.assertEqual(count.call_count,int(recall))
                self.assertEqual(sc.defer_busy_end.call_count,int(not recall))

    def test_visit_already_and_available(self):
        for done in (True,False):
            sc=VisitScenario.__new__(VisitScenario)
            sc.dev=NS(hierarchy=lambda:None); sc.click=Mock()
            sc.see=lambda key,**kw: (100,200,1) if key==('visit_stepped' if done else 'visit_step') else None
            with patch('scenarios.visit.time.sleep'):
                self.assertEqual(sc.step_once(),'already' if done else 'stepped')
            self.assertEqual(sc.click.call_count,int(not done))

    def test_pk_round_cap(self):
        self.assertEqual(PKScenario._round_limit(15,14),15)
        self.assertEqual(PKScenario._round_limit(0,4),4+PK_ROUND_CAP)
        self.assertLessEqual(PKScenario._round_limit(100,0),PK_ROUND_CAP)

    def test_friend_care_healthy_does_not_spend_items(self):
        sc=FriendCareScenario.__new__(FriendCareScenario); sc.method='ocr检测'
        sc.dev=NS(hierarchy=lambda:None)
        care=Mock(); care.read_status_ready.return_value={'体力':99,'清洁':95}
        with patch('scenarios.friend_care._FriendCare',return_value=care):
            self.assertFalse(sc.care_friend())
        care.feed.assert_not_called(); care.shower.assert_not_called()
        self.assertEqual(care.set_status_expanded.call_args_list[0].args[0],True)
        self.assertEqual(care.set_status_expanded.call_args_list[-1].args[0],False)

    def test_friend_hire_busy_defers_without_visiting(self):
        sc=FriendHireScenario.__new__(FriendHireScenario)
        sc.cfg=NS(hire_friend=NS(enabled=True,friend_name='测试',times_per_day=8))
        sc.ensure_main_page=Mock(); sc.detect_busy_remaining=Mock(return_value=('work',600))
        sc.goto_friend_home=Mock()
        with patch('scenarios.hire_friend.load_progress',return_value=('2026-09-08',0,{})),self.assertRaises(TaskDeferred):
            sc.run(max_rounds=1)
        sc.goto_friend_home.assert_not_called()

    def test_employed_recall_policy(self):
        for ready in (False,True):
            sc=EmployedScenario.__new__(EmployedScenario)
            sc.cfg=NS(employed=NS(action='等到25/75'))
            sc.ensure_main_page=Mock(); sc.leave_home=Mock(); sc.screen=Mock()
            sc.see=Mock(return_value=True); sc.employed_recall_ready=Mock(return_value=ready)
            sc._recall_employed=Mock()
            self.assertTrue(sc.run())
            self.assertEqual(sc._recall_employed.call_count,int(ready))

    def test_cross_midnight_friend_time_range(self):
        start,end=parse_time_range('22:00-02:00','test')
        for hour,wanted in [(23,True),(1,True),(12,False),(2,False)]:
            self.assertEqual(in_time_range(datetime(2026,9,8,hour).time(),start,end),wanted)

    def test_all_queue_task_gates(self):
        runner=TaskQueueRunner.__new__(TaskQueueRunner)
        now=datetime(2026,9,8,12)
        for key in ('care','school','work','adventure','visit','pk','friend_care','hire_friend'):
            task=_QueueTask(key)
            with self.subTest(task=key):
                self.assertTrue(runner._eligible(task,now))
                task.cfg.enabled=False
                self.assertFalse(runner._eligible(task,now))
                task.cfg.enabled=True; task.next_at=now+timedelta(seconds=60)
                self.assertFalse(runner._eligible(task,now))
                task.cfg.trigger='daily'; task.cfg.daily_times=['11:00']; task.dead=True
                self.assertTrue(runner._eligible(task,now))
                self.assertFalse(task.dead)

    def test_recovery_retry_stops_after_success(self):
        r=Runner.__new__(Runner)
        r._run_round=Mock(side_effect=[RuntimeError('test'),True])
        r._capture_failure_image=Mock(); r.recover=Mock()
        sc=Mock()
        self.assertTrue(r.run_one(sc,'打工'))
        r.recover.assert_not_called()

    def test_recovery_fatal_and_side_task_separation(self):
        for fatal in (False,True):
            r=Runner.__new__(Runner)
            r._run_round=Mock(side_effect=RuntimeError('test failure'))
            r._capture_failure_image=Mock(); r.recover=Mock(return_value=True)
            r._alert_and_exit=Mock(side_effect=SystemExit('test alert'))
            with self.assertRaises(SystemExit if fatal else ScenarioFailed):
                r.run_one(Mock(),'打工' if fatal else '踩踩',fatal=fatal)
            self.assertEqual(r._run_round.call_count,3)
            self.assertEqual(r._alert_and_exit.call_count,int(fatal))

    def test_pending_activity_blocks_new_main_task(self):
        r=TaskQueueRunner.__new__(TaskQueueRunner)
        pending=NS(pending={'until':datetime.now()+timedelta(minutes=10)},finish_pending=Mock())
        r._main_pending_scen=Mock(return_value=pending)
        self.assertIsNone(r._main_choice({},{}))
        pending.finish_pending.assert_not_called()

    def test_failure_backoff_is_honoured(self):
        r=TaskQueueRunner.__new__(TaskQueueRunner); t=_QueueTask('work')
        now=datetime(2026,9,8,12)
        t.cfg.failure_interval=1800
        r._fail_task(t,now,'injected test failure')
        self.assertFalse(r._eligible(t,now+timedelta(seconds=1799)))
        self.assertTrue(r._eligible(t,now+timedelta(seconds=1800)))

    def test_update_version_and_network_failure(self):
        from src.update_checker import _is_remote_newer, check_github_latest_release
        from urllib.error import URLError
        self.assertTrue(_is_remote_newer('0.7.0-local.2','v0.8.0'))
        self.assertFalse(_is_remote_newer('0.7.0-local.2','v0.7.0'))
        with patch('src.update_checker.urlopen',side_effect=URLError('offline test')):
            self.assertFalse(check_github_latest_release('owner/repo','0.7.0').ok)

    def test_coin_units_and_rightmost_coin(self):
        from src.coins import parse_coin, read_coins
        for token,expected in [('6.6k',6600),('1.2万',12000),('800',800),('错误',None)]:
            self.assertEqual(parse_coin(token),expected)
        with patch('src.coins.ocr_fullscreen',return_value=[('121',350,200,1),('6.6k',500,200,1),('23:40',100,20,1)]):
            self.assertEqual(read_coins(np.zeros((1280,720,3),dtype=np.uint8)),6600)

    def test_progress_rollover_preserves_history_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'work.json'
            store.write_raw(p,{'date':'2020-01-01','learned':3,'work_secs':100,
                               'duration':'10分钟','history':{'2019-12-31':2}})
            self.assertEqual(store.increment_daily(p),1)
            data=store.read_raw(p)
            self.assertEqual(data['duration'],'10分钟')
            self.assertEqual(data['history']['2020-01-01'],3)
            self.assertEqual(data['history']['2019-12-31'],2)
            self.assertNotIn('work_secs',data)

    def test_progress_corruption_does_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'broken.json'; p.write_text('{')
            self.assertEqual(store.read_raw(p),{})
            self.assertEqual(store.increment_daily(p),1)

    def test_config_validation_and_load(self):
        for location in WORK_LOCATIONS:
            self.assertEqual(validate_field('work.location',location),(True,location))
        self.assertFalse(validate_field('work.location','不存在')[0])
        self.assertIn(load_config().runner.engine,('task_queue','legacy'))

    def test_gui_all_pages_construct_and_switch(self):
        os.environ['QT_QPA_PLATFORM']='offscreen'
        import main
        from PyQt6.QtTest import QTest
        app=main.QApplication.instance() or main.QApplication([])
        # GUI 布局测试不访问真实模拟器管理器；设备连接另做真机测试。
        with patch.object(main.MainWindow,'_start_all'), patch.object(main.MainWindow,'_start_update_check'), patch.object(main.MainWindow,'_fill_devices'):
            window=main.MainWindow()
            self.assertFalse(any('emulator' in key for key in window._setting_widgets))
            self.assertEqual(window.stackedWidget.count(),5)
            for i in range(window.stackedWidget.count()):
                page=window.stackedWidget.widget(i)
                self.assertTrue(page.objectName())
                window.stackedWidget.setCurrentIndex(i)
                QTest.qWait(600)  # Fluent 页面切换带动画，等待完成再检查。
                self.assertEqual(window.stackedWidget.currentIndex(),i)
            window.close()

    def test_notifications_failure_is_contained(self):
        from src.notify import send_alert
        with patch('src.notify.load_config',return_value=NS(notify=NS(win_toast=True,onepush_config=''))), patch('src.notify._send_windows_toast',side_effect=RuntimeError('test')):
            self.assertFalse(send_alert('本地测试，不对外发送'))


if __name__=='__main__':
    unittest.main(verbosity=2)
