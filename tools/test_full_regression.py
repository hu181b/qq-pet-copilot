"""三轮验收共用的离线功能测试。设备输入、外部通知均为桩；进度写临时目录。"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
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
                  _bg_ticks=0,
                  _runner_proc=NS(poll=lambda: None),  # 调度器在跑，看门狗才工作
                  _disable_scrcpy=Mock(), _enable_scrcpy=Mock())
        window.btn_scrcpy.isChecked.return_value=True
        # 防抖：单次或不足 SCRCPY_THROTTLE_TICKS 轮的"后台"判定不触发暂停，
        # 只有连续 SCRCPY_THROTTLE_TICKS 轮都判后台才真正暂停一次。
        for _ in range(main.SCRCPY_THROTTLE_TICKS - 1):
            main.MainWindow._check_scrcpy(window)
        window._disable_scrcpy.assert_not_called()
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

    def test_scrcpy_watchdog_runs_before_start(self):
        # 看门狗不依赖"开始"：未跑调度器时也维护镜像（用户可能只想要画面）
        import main
        window = NS(btn_scrcpy=Mock(), _runner_proc=None,
                    _background_mirror_paused=False, _bg_ticks=0,
                    _scrcpy_retry_at=0.0, _scrcpy_proc=None,
                    _embed_tries=0, _embed_fail_logged=False,
                    _embed_timer=NS(isActive=lambda: False, start=Mock()),
                    scrcpy_view=Mock(),
                    isActiveWindow=Mock(return_value=True),
                    isMinimized=Mock(return_value=False),
                    _disable_scrcpy=Mock(), _enable_scrcpy=Mock())
        window.btn_scrcpy.isChecked.return_value = True
        # scrcpy.exe 是构建期才下载的第三方二进制（.gitignore 排除，不入库），
        # 用例里用临时文件顶替，否则干净检出（没有该目录）时必然失败。
        with tempfile.TemporaryDirectory() as folder:
            fake_scrcpy = Path(folder) / 'scrcpy.exe'
            fake_scrcpy.touch()
            with patch.object(main, 'SCRCPY', fake_scrcpy), \
                 patch('main.start_scrcpy', return_value=Mock()) as start:
                main.MainWindow._check_scrcpy(window)
        start.assert_called_once()

    def test_svip_entry_template_match(self):
        # 入口用模板匹配（同福袋套路：归一 1080 宽 + ROI 内多尺寸 matchTemplate），
        # 不写死像素坐标；合成一张贴图验证能命中且位置正确
        import cv2
        from scenarios.svip import find_entry_icon
        from src.config import resource_path
        tpl = cv2.imdecode(
            np.fromfile(resource_path('resources/svip-entry.png'), dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE)
        screen = np.full((1200, 1080, 3), 240, dtype=np.uint8)
        size = 70
        tx, ty = 900, 300  # 完整落在右侧图标列 ROI（x 880~1040, y 200~560）内
        screen[ty:ty + size, tx:tx + size] = cv2.cvtColor(
            cv2.resize(tpl, (size, size)), cv2.COLOR_GRAY2RGB)
        hit = find_entry_icon(screen)
        self.assertIsNotNone(hit)
        cx, cy, score = hit
        self.assertGreater(score, 0.90)
        self.assertLess(abs(cx - (tx + size // 2)), 10)
        self.assertLess(abs(cy - (ty + size // 2)), 10)
        # 空白屏不应命中
        self.assertIsNone(find_entry_icon(np.full((1200, 1080, 3), 240, dtype=np.uint8)))

    def test_moneybag_coins_accumulate_and_reset(self):
        # 福袋金币累计：同账号累加；换账号/配置变更清零重计
        import src.progress as prog
        from src.moneybag import parse_coins
        self.assertEqual(parse_coins('成长福袋获得120金币'), 120)
        self.assertEqual(parse_coins('获得 1,000 金币'), 1000)
        self.assertEqual(parse_coins('没有数字'), 0)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'moneybag_stats.json'
            with patch.object(prog, 'MONEYBAG_STATS_FILE', f), \
                 patch.object(prog, '_moneybag_config_sig', return_value='sig-1'):
                prog.add_moneybag_coins(50, pet_name='A')
                st = prog.add_moneybag_coins(70, pet_name='A')
                self.assertEqual(st['coins'], 120)
                self.assertEqual(st['bags'], 2)
                self.assertEqual(prog.load_moneybag_stats()['coins'], 120)
                st = prog.add_moneybag_coins(10, pet_name='B')  # 换账号 -> 清零
                self.assertEqual(st['coins'], 10)
                self.assertEqual(st['pet_name'], 'B')
            with patch.object(prog, 'MONEYBAG_STATS_FILE', f), \
                 patch.object(prog, '_moneybag_config_sig', return_value='sig-2'):
                st = prog.add_moneybag_coins(5, pet_name='B')  # 配置变更 -> 清零
                self.assertEqual(st['coins'], 5)


    def test_disable_scrcpy_hands_off_before_killing_mirror(self):
        import main
        # 切后台（permanent=False）：镜像常驻不杀、只藏窗口，不启动无头关屏。
        # scrcpy 被杀才是闪屏根因；常驻后切换时屏幕状态不变。
        window = NS(_screen_off_proc=None,
                    _scrcpy_proc=Mock(),
                    _embed_timer=Mock(),
                    scrcpy_view=Mock())
        with patch('main.start_scrcpy_screen_off') as ss_off, \
             patch('main.kill_our_scrcpy') as kill:
            main.MainWindow._disable_scrcpy(window, permanent=False)
        ss_off.assert_not_called()
        kill.assert_not_called()
        window.scrcpy_view.unembed.assert_called_once()
        # 手动关镜像开关（permanent=True）：杀镜像 + 启用无头关屏。
        screen_off = Mock(); screen_off.poll.return_value = None
        window2 = NS(_screen_off_proc=None,
                     _scrcpy_proc=Mock(),
                     _embed_timer=Mock(),
                     scrcpy_view=Mock())
        with patch('main.start_scrcpy_screen_off', return_value=screen_off) as ss_off, \
             patch('main.kill_our_scrcpy') as kill, \
             patch('main.time.sleep') as _sleep:
            main.MainWindow._disable_scrcpy(window2, permanent=True)
        ss_off.assert_called_once()
        kill.assert_called_once()
        self.assertIs(window2._screen_off_proc, screen_off)

    def test_enable_scrcpy_keeps_screen_off_if_mirror_fails(self):
        import main
        # 镜像没拉起来时不能把无头关屏进程收掉，否则屏幕被放亮。
        screen_off = Mock(); screen_off.poll.return_value = None
        window = NS(_background_mirror_paused=False,
                    _scrcpy_proc=None,
                    _screen_off_proc=screen_off,
                    _embed_tries=0, _embed_fail_logged=False,
                    _embed_timer=Mock(),
                    scrcpy_view=Mock())
        with patch('main.start_scrcpy', return_value=None) as ss, \
             patch('main.time.sleep') as _sleep:
            main.MainWindow._enable_scrcpy(window)
        ss.assert_called_once()
        screen_off.terminate.assert_not_called()
        self.assertIs(window._screen_off_proc, screen_off)

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

    # ---- SVIP 礼包（每日领取；非会员自动关任务） ----

    @staticmethod
    def _svip_scenario(states):
        """构造裸 SvipScenario：see 按 states 字典应答，设备交互全部 Mock。"""
        from scenarios.svip import SvipScenario
        sc = SvipScenario.__new__(SvipScenario)
        sc.click = Mock()
        sc.screen = Mock(return_value=object())
        sc.see = lambda key, *a, **kw: states.get(key)
        sc.ensure_main_page = Mock()
        sc._close_dialog = Mock()
        return sc

    def test_svip_claim_success(self):
        # 会员且今日未领：点"立即领取" -> 记进度 -> 返回 True
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_claim': (100, 300, 1)})
        with patch('scenarios.svip.time.sleep'), \
             patch('scenarios.svip.svip_claimed_today', return_value=False), \
             patch('scenarios.svip.save_svip_claim') as save:
            self.assertTrue(sc.run())
        save.assert_called_once_with(True)
        self.assertGreaterEqual(sc.click.call_count, 2)  # 入口 + 领取按钮
        sc.ensure_main_page.assert_called()

    def test_svip_already_claimed_today_skips(self):
        # 进度文件已标记今天领取：直接跳过，不碰设备
        from scenarios.svip import SvipScenario
        sc = SvipScenario.__new__(SvipScenario)
        sc.ensure_main_page = Mock()
        with patch('scenarios.svip.svip_claimed_today', return_value=True), \
             patch('scenarios.svip.save_svip_claim') as save:
            self.assertFalse(sc.run())
        save.assert_not_called()
        sc.ensure_main_page.assert_not_called()

    def test_svip_tomorrow_dialog_marks_done(self):
        # 弹窗按钮是"明日再来"：说明今天已领过，记进度并返回 False
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_tomorrow': (100, 300, 1)})
        with patch('scenarios.svip.time.sleep'), \
             patch('scenarios.svip.svip_claimed_today', return_value=False), \
             patch('scenarios.svip.save_svip_claim') as save:
            self.assertFalse(sc.run())
        save.assert_called_once_with(True)
        sc.click.assert_called_once()  # 只点了入口，没点领取

    def test_svip_non_member_disables_task(self):
        # 非会员：弹窗按钮是"开通 SVIP" -> 自动关闭任务并返回 False，不记领取进度
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_open': (100, 300, 1)})
        with patch('scenarios.svip.time.sleep'), \
             patch('scenarios.svip.svip_claimed_today', return_value=False), \
             patch('scenarios.svip.save_svip_claim') as save, \
             patch('scenarios.svip.SvipScenario._disable_task') as disable:
            self.assertFalse(sc.run())
        disable.assert_called_once()
        save.assert_not_called()

    def test_svip_reward_popup_means_claimed(self):
        # 真机 bug 回归：会员未领取时点帽图标是"直接发奖"并弹"恭喜获得"奖励页
        # （没有"立即领取"按钮），必须判定为领取成功并记进度
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_reward': (100, 300, 1)})
        with patch('scenarios.svip.save_svip_claim') as save, \
             patch.object(sc, '_close_dialog') as close:
            self.assertTrue(sc._claim_once())
        save.assert_called_once_with(True)
        close.assert_called_once()

    def test_svip_probe_membership_reward_is_member(self):
        # 探测时撞上"刚领到"的奖励页 -> 也是会员，并顺手记当天已领
        sc = self._svip_scenario({})
        sc._open_and_read_state = Mock(return_value=('reward', object()))
        with patch('scenarios.svip.save_svip_claim') as save:
            self.assertTrue(sc.probe_membership())
        save.assert_called_once_with(True)

    def test_svip_disable_task_writes_config(self):
        # _disable_task 把 tasks.svip.enabled=false 写回 config.yaml，并打非会员标记
        from scenarios.svip import SvipScenario
        data = {'tasks': {'svip': {'enabled': True}}}
        with patch('src.settings.load_raw', return_value=data), \
             patch('src.settings.save_raw') as save_raw, \
             patch('scenarios.svip.set_svip_nonmember') as mark:
            SvipScenario._disable_task()
        save_raw.assert_called_once()
        self.assertFalse(data['tasks']['svip']['enabled'])
        mark.assert_called_once_with(True)

    def test_svip_probe_membership_states(self):
        # 会员探测：明日再来/立即领取 -> True（顺手记领取进度）；开通 SVIP -> False
        sc = self._svip_scenario({'svip_entry': (10, 20, 1),
                                  'svip_dialog': (100, 200, 1),
                                  'svip_tomorrow': (100, 300, 1)})
        sc._open_and_read_state = Mock(return_value=('tomorrow', object()))
        with patch('scenarios.svip.save_svip_claim') as save:
            self.assertTrue(sc.probe_membership())
        save.assert_called_once_with(True)
        sc._open_and_read_state = Mock(return_value=('open', object()))
        self.assertFalse(sc.probe_membership())
        sc._open_and_read_state = Mock(return_value=(None, object()))
        self.assertIsNone(sc.probe_membership())  # 识别不了不当成状态变化

    def test_svip_recheck_reopens_task_for_member(self):
        # 非会员自动关闭期间：每天探测一次，恢复会员 -> 自动写回 enabled=true
        from datetime import time as dtime
        from scenarios.runner import TaskQueueRunner, _QueueTask
        runner = TaskQueueRunner.__new__(TaskQueueRunner)
        runner.svip = Mock()
        runner.svip.probe_membership.return_value = True
        runner._latest_daily_time = Mock(return_value=dtime(9, 5))
        task = _QueueTask('svip')
        task.cfg.enabled = False
        data = {'tasks': {'svip': {'enabled': False}}}
        with patch('scenarios.runner.svip_nonmember_flag', return_value=True), \
             patch('src.settings.load_raw', return_value=data), \
             patch('src.settings.save_raw') as save_raw:
            runner._svip_nonmember_recheck({'svip': task})
            # 同一天不重复探测
            runner._svip_nonmember_recheck({'svip': task})
        self.assertEqual(runner.svip.probe_membership.call_count, 1)
        save_raw.assert_called_once()
        self.assertTrue(data['tasks']['svip']['enabled'])
        # 手动关闭（无非会员标记）：不探测
        with patch('scenarios.runner.svip_nonmember_flag', return_value=False), \
             patch('src.settings.save_raw') as save_raw2:
            runner._svip_nonmember_recheck({'svip': task})
        save_raw2.assert_not_called()

    def test_migrate_tasks_order_appends_new_keys(self):
        # 老配置 tasks.order 缺 svip：启动迁移自动追加到队尾；已有则不写盘
        from src import settings as settings_mod
        data = {'tasks': {'order': 'care>work'}}
        with patch.object(settings_mod, 'load_raw', return_value=data), \
             patch.object(settings_mod, 'save_raw') as save_raw:
            settings_mod.migrate_tasks_order()
        save_raw.assert_called_once()
        self.assertEqual(data['tasks']['order'], 'svip>care>work')
        data2 = {'tasks': {'order': 'care>svip>work'}}
        with patch.object(settings_mod, 'load_raw', return_value=data2), \
             patch.object(settings_mod, 'save_raw') as save_raw2:
            settings_mod.migrate_tasks_order()
        save_raw2.assert_not_called()


    def test_wait_employed_back_exits_when_employment_ends(self):
        # 真机 bug 回归：雇佣自然到期回主页（面板消失）后，等待循环必须退出，
        # 不能无限空转卡死整个调度器
        from scenarios.employed import EmployedScenario
        sc = EmployedScenario.__new__(EmployedScenario)
        sc.dev = NS(click=lambda *a: None)
        sc.screen = Mock(return_value=object())
        sc.employed_recall_ready = Mock(return_value=False)
        sc.ensure_main_page = Mock()
        sc._recall_employed = Mock()
        # 前两轮还能看到"被雇佣中"，之后连续消失 -> 应退出且不召回
        states = [(10, 20, 1), (10, 20, 1), None, None, None, None]
        sc.see = lambda key, screen=None, source=None: (
            states.pop(0) if key == 'employed_in' else None)
        with patch('src.scenario.time.sleep'):
            sc.wait_employed_back(check_interval=0.01)
        sc._recall_employed.assert_not_called()
        sc.ensure_main_page.assert_called_once()

    def test_wait_employed_back_recalls_when_ready(self):
        # 召回条件满足：正常走召回，不受"面板消失"退出影响
        from scenarios.employed import EmployedScenario
        sc = EmployedScenario.__new__(EmployedScenario)
        sc.dev = NS(click=lambda *a: None)
        sc.screen = Mock(return_value=object())
        sc.employed_recall_ready = Mock(return_value=True)
        sc.see = Mock(return_value=(10, 20, 1))
        sc._recall_employed = Mock()
        sc.ensure_main_page = Mock()
        with patch('src.scenario.time.sleep'):
            sc.wait_employed_back(check_interval=0.01)
        sc._recall_employed.assert_called_once()

    def test_reset_daily_progress_for_pet(self):
        # 换宠物：当天计数清零、历史保留；没有计数时不产生重复重置项
        import src.progress as prog
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            files = {n: p / f'{n}.json' for n in
                     ('school', 'work', 'adventure', 'employed',
                      'visit', 'pk', 'hire_friend', 'exp', 'svip')}
            consts = {'SCHOOL_PROGRESS_FILE': files['school'],
                      'WORK_PROGRESS_FILE': files['work'],
                      'ADVENTURE_PROGRESS_FILE': files['adventure'],
                      'EMPLOYED_PROGRESS_FILE': files['employed'],
                      'VISIT_PROGRESS_FILE': files['visit'],
                      'PK_PROGRESS_FILE': files['pk'],
                      'HIRE_FRIEND_PROGRESS_FILE': files['hire_friend'],
                      'EXP_DAILY_PROGRESS_FILE': files['exp'],
                      'SVIP_PROGRESS_FILE': files['svip']}
            with patch.multiple(prog, **consts):
                # 用真实当天日期：进度文件按天判定，写死日期会在跨天后失效
                today_str = date.today().isoformat()
                prog.save_progress(files['school'], today_str, 3, {'2026-09-18': 2})
                prog.save_progress(files['visit'], today_str, 10, {})
                prog.save_exp_daily(True, today_str, {})
                prog.save_svip_claim(True, today_str, {})
                reset = prog.reset_daily_progress_for_pet('咕咕嘎嘎')
                self.assertIn('学习', reset)
                self.assertIn('踩踩', reset)
                self.assertIn('经验日常', reset)
                self.assertIn('SVIP礼包', reset)
                today, done, history = prog.load_progress(files['school'], quiet=True)
                self.assertEqual(done, 0)
                self.assertEqual(history.get('2026-09-18'), 2)  # 历史保留
                self.assertEqual(prog.load_progress(files['visit'], quiet=True)[1], 0)
                _, exp_done, _ = prog.load_exp_daily(quiet=True)
                self.assertFalse(exp_done)
                # 第二次调用：已无计数，不再列出
                self.assertEqual(prog.reset_daily_progress_for_pet('咕咕嘎嘎'), [])

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
        for key in ('care','school','work','adventure','visit','pk','friend_care','hire_friend','svip'):
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
