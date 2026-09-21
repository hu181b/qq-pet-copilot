# -*- coding: utf-8 -*-
"""每日领取 QQ SVIP 会员礼包场景。

入口：主页宠物状态卡右侧图标列的企鹅帽（图标旁偶尔显示"点击有礼"标签）。
定位顺序：content-desc / OCR 的"点击有礼"标签 -> 模板匹配图标本身
（find_entry_icon，分辨率无关，见下方常量）。点开是"QQ SVIP专属礼包"弹窗，
四种状态：
- 会员且今日未领：真机实测**点帽图标即直接发放**，弹"恭喜获得SVIP专属礼包"
  奖励页（没有"立即领取"按钮）——识别到"恭喜获得"即判领取成功，记进度后收尾；
- 会员且今日已领：按钮是"明日再来"，记进度后当天不再执行；
- 非会员：按钮是"开通 SVIP"——关掉弹窗，并把 tasks.svip.enabled=false 写回
  config.yaml 自动关闭该任务（调度器 reload_config 每轮热加载，下一轮即生效），
  同时打"非会员"标记：之后每天复查一次，恢复会员自动重开；
- "立即领取"按钮作为兜底分支保留（个别机型/版本可能走按钮确认）。

调度走任务队列：tasks.svip.trigger=daily + daily_times 每天到点执行一次；
"今天是否已领取"持久化在 runs/svip_progress.json（跨天自动失效）。
"""
from __future__ import annotations

import time
from functools import lru_cache

from src.config import resource_path
from src.progress import (
    log,
    save_svip_claim,
    set_svip_nonmember,
    svip_claimed_today,
)
from src.scenario import CLICK_INTERVAL, DeviceScenario

# 找入口/等弹窗的重试轮数（每轮间隔 CLICK_INTERVAL）
ENTRY_ATTEMPTS = 3
DIALOG_ATTEMPTS = 5
# 点"立即领取"后等奖励展示/弹窗刷新的轮数
CLAIM_SETTLE_ATTEMPTS = 5
# "开通 SVIP"复核等待（秒）：弹窗按钮区可能先渲染开通模板再刷新
OPEN_RECHECK_WAIT = 1.5
# 等弹窗按钮出现的轮数（标题已出但按钮区可能还在加载）
STATE_ATTEMPTS = 4
# ---- 企鹅帽入口的模板匹配（照 src/moneybag.py friend_bag_points 的套路）----
# 截图统一归一到 1080 宽后，只在右侧图标列这个 ROI 里多尺寸 matchTemplate：
# 全屏匹配（得分 ~0.6）区分度不够，限定 ROI 后真机得分 0.99 且坐标精确
ENTRY_ROI = (880, 1040, 200, 560)   # 1080 归一坐标下的 (x1, x2, y1, y2)
ENTRY_TEMPLATE_SIZES = (52, 58, 64, 70, 76)
ENTRY_MATCH_SCORE = 0.90            # 低于该分不认（福袋用的是 0.94）


@lru_cache(maxsize=1)
def _entry_template():
    """企鹅帽参考图（np.fromfile 读是为了兼容中文路径）。资源缺失返回 None：
    打包漏带时降级为只靠标签定位，不能让整个任务崩掉触发异常恢复。"""
    import cv2
    import numpy as np

    try:
        data = np.fromfile(resource_path('resources/svip-entry.png'), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    except OSError as e:
        log(f'SVIP 入口模板图缺失（{e}），本次只用"点击有礼"标签定位')
        return None


def find_entry_icon(screen):
    """模板匹配找企鹅帽入口，命中返回 (x, y, score)，否则 None。

    与 src/moneybag.py 的 friend_bag_points 同一套路：截图归一到 1080 宽
    （分辨率无关），只在右侧图标列 ROI 内多尺寸 matchTemplate，取最高分。
    """
    import cv2

    template = _entry_template()
    if template is None:
        return None
    h, w = screen.shape[:2]
    scale = 1080 / w
    gray = cv2.resize(cv2.cvtColor(screen, cv2.COLOR_RGB2GRAY),
                      (1080, round(h * scale)))
    x1, x2, y1, y2 = ENTRY_ROI
    x2, y2 = min(x2, gray.shape[1]), min(y2, gray.shape[0])
    roi = gray[y1:y2, x1:x2]
    best = None
    for size in ENTRY_TEMPLATE_SIZES:
        tile = cv2.resize(template, (size, size))
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(roi, tile, cv2.TM_CCOEFF_NORMED))
        if score >= ENTRY_MATCH_SCORE and (best is None or score > best[2]):
            best = (round((x1 + point[0] + size / 2) / scale),
                    round((y1 + point[1] + size / 2) / scale), float(score))
    return best


class SvipScenario(DeviceScenario):
    """每日领取 QQ SVIP 会员礼包；非会员自动关闭该任务。"""

    def run(self, max_times: int | None = None, max_rounds: int = 0) -> bool:
        """执行一次领取流程。

        返回 True = 本次领取成功（调度按 success_interval 排下次，但当天
        进度已标记，svip_due 会挡住重复执行）；返回 False = 当天无需/不可
        再执行（已领取或非会员已自动关任务），任务队列会把当天标记为结束。
        找不到入口/弹窗异常则抛错，交给调度器的失败退避处理。
        """
        if svip_claimed_today():
            log('SVIP礼包: 今天已领取过，跳过')
            return False
        log('SVIP礼包: 尝试领取每日会员礼包')
        self.ensure_main_page()
        try:
            return self._claim_once()
        finally:
            self.ensure_main_page()

    # ---- 主流程 ----

    def _claim_once(self) -> bool:
        # state 已在 _open_and_read_state 里轮询读取并复核过"开通 SVIP"
        state, screen = self._open_and_read_state()

        if state == 'open':
            log('SVIP礼包弹窗按钮是"开通 SVIP"（复核后仍是）：当前账号不是 SVIP 会员，'
                '关闭弹窗并自动关闭 SVIP礼包 任务')
            self._close_dialog(screen)
            self._disable_task()
            return False

        if state == 'reward':
            # 点帽图标后直接发奖（真机实测没有"立即领取"按钮），奖励页就是成功凭证
            log('SVIP礼包: 已领取成功（弹窗显示"恭喜获得SVIP专属礼包"）')
            save_svip_claim(True)
            self._close_dialog(screen)
            return True

        if state == 'tomorrow':
            log('SVIP礼包弹窗按钮是"明日再来"：今天已领取过')
            save_svip_claim(True)
            self._close_dialog(screen)
            return False

        if state != 'claim':
            raise RuntimeError('SVIP 弹窗未识别到 领取/明日再来/开通SVIP 任一按钮')
        claim = self.see('svip_claim', screen)
        self.click(claim[0], claim[1])
        # 领取后可能有奖励展示/弹窗刷新，等到"恭喜获得"或"明日再来"或弹窗消失再收尾
        for _ in range(CLAIM_SETTLE_ATTEMPTS):
            time.sleep(CLICK_INTERVAL)
            screen = self.screen()
            if (self.see('svip_reward', screen) or self.see('svip_tomorrow', screen)
                    or not self.see('svip_dialog', screen)):
                break
        save_svip_claim(True)
        log('SVIP礼包: 每日会员礼包领取成功')
        self._close_dialog(screen)
        return True

    # ---- 分步 ----

    def _open_and_read_state(self):
        """点入口 -> 等弹窗 -> 轮询读状态（含"开通 SVIP"复核）。

        返回 (state, screen)；state: 'reward'（领取成功奖励页）/'tomorrow'
        （今日已领）/'claim'（领取按钮，兜底）/'open'（非会员）/None（没识别出来）。
        识别不出来就抛错交失败退避。
        """
        hit = self._find_entry()
        if hit is None:
            raise RuntimeError('主页未找到 SVIP 礼包入口（点击有礼）')
        self.click(hit[0], hit[1])
        screen = self._wait_dialog()
        # 标题出了按钮区可能还在加载：轮询等按钮状态出现，避免误报"没识别到按钮"
        state = None
        for attempt in range(1, STATE_ATTEMPTS + 1):
            state = self._dialog_state(screen)
            if state is not None:
                break
            log(f'等待礼包弹窗按钮出现 ({attempt}/{STATE_ATTEMPTS})')
            time.sleep(CLICK_INTERVAL)
            screen = self.screen()
        if state == 'open':
            # 弹窗按钮区可能先渲染"开通 SVIP"模板再刷新成实际状态
            # （真机见过同一入口一次"开通 SVIP"、一次"明日再来"），复核一轮防误判，
            # 误判一次就会把任务误关。
            time.sleep(OPEN_RECHECK_WAIT)
            screen = self.screen()
            state = self._dialog_state(screen)
        return state, screen

    def probe_membership(self) -> bool | None:
        """会员状态探测（非会员自动关闭期间每日复查用）。

        打开礼包弹窗看状态：'reward'/'claim'/'tomorrow' 都是会员 -> True
        （顺手把"刚领到"或"今日已领"记成当天已领取）；复核后仍是 'open'
        -> False；识别不了 -> None（不当成状态变化，明天再试）。
        """
        self.ensure_main_page()
        try:
            state, screen = self._open_and_read_state()
        except Exception:
            self.ensure_main_page()  # 弹窗可能没开成功，尽量回主页再抛
            raise
        try:
            if state in ('reward', 'claim', 'tomorrow'):
                if state in ('reward', 'tomorrow'):
                    save_svip_claim(True)  # 刚领取成功 / 今天已领取
                return True
            if state == 'open':
                return False
            return None
        finally:
            if state is not None:
                self._close_dialog(screen)
            self.ensure_main_page()

    def _dialog_state(self, screen) -> str | None:
        """识别弹窗当前状态：'reward'（领取成功奖励页）/ 'tomorrow'（今日已领）/
        'claim'（领取按钮，兜底）/ 'open'（非会员）；都不匹配返回 None。

        顺序：先"恭喜获得"（领取成功的强特征），再已领取、领取按钮、开通，
        避免 OCR 碎片互相误命中。
        """
        if self.see('svip_reward', screen):
            return 'reward'
        if self.see('svip_tomorrow', screen):
            return 'tomorrow'
        if self.see('svip_claim', screen):
            return 'claim'
        if self.see('svip_open', screen):
            return 'open'
        return None

    def _find_entry(self) -> tuple[int, int, float] | None:
        """在主页找企鹅帽入口，重试 ENTRY_ATTEMPTS 轮。

        先试 content-desc / OCR 的"点击有礼"标签（标签并非一直渲染），
        再模板匹配图标本身（find_entry_icon），都不中才算没找到。
        """
        for attempt in range(1, ENTRY_ATTEMPTS + 1):
            screen = self.screen()
            hit = self.see('svip_entry', screen)
            if hit is None:
                hit = find_entry_icon(screen)
            if hit:
                return hit
            log(f'主页未找到 SVIP 礼包入口，等待重试 ({attempt}/{ENTRY_ATTEMPTS})')
            time.sleep(CLICK_INTERVAL)
        return None

    def _wait_dialog(self):
        """点入口后等礼包弹窗出现，返回弹窗页截图；超时抛错交失败退避。"""
        for attempt in range(1, DIALOG_ATTEMPTS + 1):
            time.sleep(CLICK_INTERVAL)
            screen = self.screen()
            if self.see('svip_dialog', screen):
                return screen
            log(f'等待 SVIP 礼包弹窗出现 ({attempt}/{DIALOG_ATTEMPTS})')
        raise RuntimeError('点击入口后未出现 SVIP 礼包弹窗')

    def _close_dialog(self, screen=None) -> None:
        """关掉礼包弹窗：优先点小关闭按钮（content-desc 关闭），否则系统返回键。

        奖励页（"恭喜获得…"+提示"点击空白处关闭"）实测没有关闭控件，走返回键；
        关完确认弹窗确实消失，没消失再补一次返回。"""
        for attempt in (1, 2):
            try:
                source = self.dev.hierarchy()
                hit = self.see('svip_close', None, source)
            except Exception:
                hit = None
            if hit:
                self.click(hit[0], hit[1])
            else:
                log('SVIP礼包: 未找到关闭按钮，按系统返回键关弹窗')
                self.go_back()
            time.sleep(CLICK_INTERVAL)
            try:
                if not self.see('svip_dialog', self.screen()):
                    return
            except Exception:
                return  # 截图/OCR 失败不纠缠，交回主页面检查
            log('SVIP礼包: 弹窗仍未消失，再按一次返回键')
        log('SVIP礼包: 弹窗未能确认关闭，交回主页面检查处理')

    @staticmethod
    def _disable_task() -> None:
        """非会员：把 tasks.svip.enabled=false 写回 config.yaml（照 u2dev
        回退控制方案的范式）。调度器 reload_config 每轮热加载，下一轮即生效；
        GUI 任务表每秒刷新，开关会显示为关。"""
        try:
            from src.settings import load_raw, save_raw, set_value
            data = load_raw()
            set_value(data, 'tasks.svip.enabled', False)
            save_raw(data)
            set_svip_nonmember(True)  # 打"非会员自动关闭"标记：调度器每日复查会员状态
            log('已把 tasks.svip.enabled=false 写回 config.yaml'
                '（非会员不再调度该任务；关闭期间每天自动复查一次会员状态，'
                '恢复了会自动重新开启；也可手动在任务表把开关打开）')
        except Exception as e:  # noqa: BLE001 - 写配置失败不阻断本次流程
            log(f'写回 config.yaml 失败（{e}），请手动把 SVIP礼包 任务开关关闭')
