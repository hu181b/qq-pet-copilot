"""异常恢复：重新进入 QQ 宠物页面。

调度/场景抛异常（设备卡死、u2 连接断开、游戏界面卡死等）时的恢复链路，
按配置 recover.method 二选一：
- 重启设备（默认）：adb reboot -> 等开机完成 -> 亮屏上滑解锁（仅滑动锁屏）-> 启动 QQ；
- 重启游戏：只强停 QQ 再重开（设备不重启，快），适合游戏界面卡死；
之后统一：等待并紧凑双击 Q宠-* 入口进宠物页面（minitouch 两连击，间隔
~0.03s；真机验证 0.3s 会被识别成两次单击，d.click 走 JSON-RPC 单次往返就
可能超窗，必须用 minitouch 压间隔）-> 返回新的 U2Device 连接（旧连接随
重启失效），由调用方刷新各场景的 dev 后继续后续任务。

QQ 宠物入口的 content-desc 形如 "Q宠-1000004"，后缀数字随账号/宠物不固定，
按 descriptionStartsWith 前缀匹配。
"""
from __future__ import annotations

import subprocess
import sys
import time

from .adb.device import Device
from .progress import log
from .opener import _open_pet_via_scheme, _wait_qq_settle
from .u2dev import U2Device

# Windows 下隐藏子进程的命令行窗口
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

QQ_PACKAGE = 'com.tencent.mobileqq'
# QQ 宠物入口的 content-desc 前缀（完整值形如 "Q宠-1000004"，后缀数字不固定）
PET_ENTRY_DESC_PREFIX = 'Q宠-'

BOOT_TIMEOUT = 180.0        # adb reboot 后等开机完成的超时（秒）
BOOT_POLL_INTERVAL = 5.0
U2_CONNECT_TIMEOUT = 60.0   # 开机后等 atx-agent 就绪、u2 可连的超时（秒）
U2_CONNECT_INTERVAL = 5.0
PET_ENTRY_TIMEOUT = 120.0   # 启动 QQ 后等 Q宠-* 入口出现的超时（秒）
PET_ENTRY_POLL_INTERVAL = 3.0
PET_ENTRY_CLICK_TRIES = 3   # 点入口后宠物页没出来时的重试点击次数
PET_ENTRY_SETTLE_SECONDS = 0.5  # 找到入口后等页面稳定再点的时间（原 2s，太慢；
                                # 点不进主页有 back 退回重试兜底，不用等那么久）
PET_ENTRY_DOUBLE_CLICK_INTERVAL = 0.03  # 紧凑双击两击间隔（秒）：minitouch 事件
                                        # 本地直发，0.03s 足够压进应用双击窗口
PET_PAGE_TIMEOUT = 15.0    # 每次点击后等宠物主页加载的超时（秒，冷启动可能要十几秒）
PET_PAGE_POLL_INTERVAL = 3.0
SCHEME_TRY_ROUNDS = 2      # 进宠物页先试官方 scheme 直开的轮数（失败回退点击 Q宠-* 入口）


def reenter_pet(adb: Device, method: str = "重启设备") -> U2Device:
    """重启手机或 QQ，通过官方入口恢复宠物主页。"""
    if method == "重启游戏":
        # 只重开 QQ，不重启设备（快；设备级卡死/u2 挂掉时治不了）
        log('异常恢复：重启 QQ 游戏（不重启设备）...')
        adb.force_stop_app(QQ_PACKAGE)
        dev = _connect_u2(adb)
    else:
        log('异常恢复：adb reboot 重启设备...')
        adb.reboot_and_wait(BOOT_TIMEOUT, BOOT_POLL_INTERVAL)
        dev = _connect_u2(adb)
        _unlock(dev)
    log('启动 QQ...')
    adb.launch_app(QQ_PACKAGE)
    # 先试官方 scheme 直开宠物主页（JumpActivity，零点击零权限，比点入口稳定）；
    # 平板身份（ro.build.characteristics 含 tablet）门禁会拦 scheme，跳过直开
    characteristics = ''
    try:
        characteristics = adb.getprop('ro.build.characteristics')
    except Exception:  # noqa: BLE001 - 读不到按非平板处理，正常尝试
        pass
    if 'tablet' not in characteristics.lower():
        _wait_qq_settle(adb.adb, adb.serial)
        for attempt in range(1, SCHEME_TRY_ROUNDS + 1):
            _open_pet_via_scheme(adb.adb, adb.serial)
            if _wait_main_page(dev):
                log('已通过官方 scheme 直开进入宠物主页')
                return dev
            log(f'scheme 直开后宠物主页未出现，重试 ({attempt}/{SCHEME_TRY_ROUNDS})')
        log('scheme 直开未成功，回退点击 Q宠-* 入口')
    else:
        log(f'设备为平板身份（{characteristics}），跳过 scheme 直开')
    for attempt in range(1, PET_ENTRY_CLICK_TRIES + 1):
        _click_pet_entry(dev)
        if _wait_main_page(dev):
            return dev
        log(f'点击入口后宠物主页未出现，重试点击 ({attempt}/{PET_ENTRY_CLICK_TRIES})')
        # 双击可能落在"单击页"（入口已不在当前页）：back 退回 QQ 入口页再重试，
        # 避免在错误页面空等入口出现直到超时；入口还在（点击被吞）则直接重试
        try:
            if not dev.d(descriptionStartsWith=PET_ENTRY_DESC_PREFIX).exists:
                log('未检测到宠物入口（可能进了单击页），按 back 退回')
                dev.d.press('back')
                time.sleep(PET_ENTRY_POLL_INTERVAL)
        except Exception as e:
            log(f'退回单击页失败: {e}')
    raise RuntimeError(f'点击 {PET_ENTRY_CLICK_TRIES} 次宠物入口仍未进入宠物页面')












def _click_pet_entry(dev: U2Device) -> None:
    """等 Q宠-* 入口出现并紧凑双击进入。

    真机调试结论：单击只选中不跳转；双击间隔 0.3s 会被识别成两次单击（进单击页），
    间隔必须压进应用很短的双击判定窗口。d.click 走 JSON-RPC（单次往返几十~几百
    ms），两次 click 的物理间隔不可控；改用 minitouch 两连击（本地直发，间隔
    ~0.03s）。点完若主页没出，外层 reenter_pet 会 back 退回入口页重试。
    """
    log(f'等待 QQ 宠物入口（{PET_ENTRY_DESC_PREFIX}*）出现...')
    deadline = time.monotonic() + PET_ENTRY_TIMEOUT
    while True:
        ui = dev.d(descriptionStartsWith=PET_ENTRY_DESC_PREFIX)
        if ui.exists:
            x, y = ui.center()
            log(f'找到 QQ 宠物入口 ({int(x)}, {int(y)})，紧凑双击进入宠物页面')
            time.sleep(PET_ENTRY_SETTLE_SECONDS)  # 入口刚渲染出来时点击无效，短等页面稳定
            _compact_double_click(dev, int(x), int(y))
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f'启动 QQ 后 {PET_ENTRY_TIMEOUT:.0f}s 内未出现宠物入口'
                f'（{PET_ENTRY_DESC_PREFIX}*）')
        time.sleep(PET_ENTRY_POLL_INTERVAL)


def _compact_double_click(dev: U2Device, x: int, y: int) -> None:
    """minitouch 两连击进入宠物入口（间隔 ~0.03s，压进应用双击窗口）。

    用 minitouch 事件（d.touch.down/up）而不是 d.click：d.click 是 JSON-RPC 调用，
    单次往返几十~几百 ms，两次 click 的物理间隔容易超 0.3s，被应用识别成
    两次单击，落在"单击页"而不是宠物主页。
    """
    dev.touch_down(x, y)
    dev.touch_up(x, y)
    time.sleep(PET_ENTRY_DOUBLE_CLICK_INTERVAL)
    dev.touch_down(x, y)
    dev.touch_up(x, y)


def _wait_main_page(dev: U2Device) -> bool:
    """等宠物主页（"宠物状态"容器）出现；QQ/游戏冷启动加载可能要十几秒。"""
    deadline = time.monotonic() + PET_PAGE_TIMEOUT
    while time.monotonic() < deadline:
        if dev.d(description='宠物状态').exists:
            log('已进入宠物页面')
            return True
        time.sleep(PET_PAGE_POLL_INTERVAL)
    return False


def _unlock(dev: U2Device) -> None:
    """开机后若停在待解锁页面：亮屏并上滑解开滑动锁屏。

    只对无密码的滑动锁屏有效；密码/图案锁 adb 层解不开，
    后续等 Q宠-* 入口会超时，恢复失败（日志会体现）。
    """
    dev.d.screen_on()
    w, h = dev.window_size()
    dev.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), duration=0.3)
    time.sleep(1)


def _connect_u2(adb: Device) -> U2Device:
    """开机后 atx-agent 就绪需要几秒，重试直到 u2 可连。"""
    deadline = time.monotonic() + U2_CONNECT_TIMEOUT
    while True:
        try:
            return U2Device(adb.adb, adb.serial)
        except Exception as e:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f'开机后 {U2_CONNECT_TIMEOUT:.0f}s 内 u2 连接失败: {e}') from None
            log(f'u2 暂不可连，{U2_CONNECT_INTERVAL:.0f}s 后重试: {e}')
            time.sleep(U2_CONNECT_INTERVAL)
