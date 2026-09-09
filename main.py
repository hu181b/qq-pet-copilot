"""启动入口（PyQt6 GUI，界面基于 PyQt6-Fluent-Widgets）：左侧 Fluent 导航栏 +
右侧内容区（顶部全局工具栏 + 页面堆栈）。

- 启动前清理本设备遗留的 scrcpy（只清本实例/本设备，多开互不影响），再重新拉起并以 --window-borderless 嵌入
- scrcpy 以 --turn-screen-off 运行（手机屏幕关闭，镜像照常）
- 顶部工具栏"开始/停止"按钮：开始 = 子进程启动调度器，停止 = 立即结束调度器进程
- 导航页：主页（画面+状态+今日统计+日志）/调度/统计（各任务近 N 天平滑折线图）/任务/设置
- 调度器子进程的 stdout 实时显示在主页日志卡片
- scrcpy 看门狗：进程断开（设备 adb reboot/掉线）后自动重拉并重嵌入
- 关闭窗口时结束由本程序拉起的 scrcpy 和调度器进程

运行：python main.py
控制台模式（无 GUI）：python scenarios/runner.py
"""

import os
import queue
import re
import subprocess
import sys
import threading
import time
import zlib
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTime, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QSizePolicy,
    QSizeGrip,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    EditableComboBox,
    FluentIcon as FIF,
    HeaderCardWidget,
    HyperlinkLabel,
    LineEdit,
    MessageBox,
    MSFluentWindow,
    NavigationItemPosition,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    SpinBox,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
    Theme,
    TimeEdit,
    setTheme,
)

from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from qfluentwidgets.components.navigation.navigation_bar import NavigationBar
from qfluentwidgets.components.navigation.navigation_widget import NavigationPushButton

import win32con
import win32gui
import win32process

from src import settings as settings_io
from src.adb.device import Device
from src.config import (
    APP_ROOT,
    PROJECT_ROOT,
    PROFILE_ID,
    TASK_KEYS,
    find_adb,
    load_config,
    resource_path,
)
from src.progress import (
    ADVENTURE_PROGRESS_FILE,
    EMPLOYED_PROGRESS_FILE,
    HIRE_FRIEND_PROGRESS_FILE,
    PK_PROGRESS_FILE,
    SCHOOL_PROGRESS_FILE,
    VISIT_PROGRESS_FILE,
    WORK_PROGRESS_FILE,
    add_log_listener,
    load_durations,
    load_exp_daily,
    load_progress,
    log,
)
from src.stats_chart import StatsPanel
from src.status_cache import FIELDS as STATUS_FIELDS
from src.status_cache import load_accounts
from src.queue_status import load_queue_status
from src.version import APP_GITHUB_REPO, APP_RELEASES_URL, APP_VERSION

# 仅类型检查用：U2Device 在方法内懒加载导入，注解里引用它需要类型检查器能解析
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.u2dev import U2Device

SCRCPY = resource_path('resources/scrcpy-win64') / 'scrcpy.exe'
SCRCPY_TITLE_PREFIX = 'QQPetCopilotScrcpy'
RUNNER_SCRIPT = APP_ROOT / 'scenarios' / 'runner.py'
EMBED_TRIES = 40  # 查找 scrcpy 窗口的次数（每次 500ms）
LOG_MAX_LINES = 5000  # 日志区显示行数上限（超出自动丢弃最旧的行；完整日志在 runs/logs/ 文件里）
SCRCPY_WATCHDOG_MS = 5000    # scrcpy 看门狗轮询间隔（毫秒）
SCRCPY_RETRY_INTERVAL = 15.0  # 重拉失败后的退避（秒；设备重启要几十秒，别刷日志）
UPDATE_CHECK_INTERVAL_MS = 6 * 3600 * 1000  # 检查更新周期（启动后先自动查一次）

# Windows 下隐藏子进程的命令行窗口（scrcpy/taskkill 等都是控制台程序）
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

# OnePush 各提供方参数配置教程（ALAS wiki 中文文档）
ONEPUSH_HELP_URL = ('https://github.com/LmeSzinc/AzurLaneAutoScript'
                    '/wiki/Onepush-configuration-%5BCN%5D')

# 主题设置项（gui.theme）-> qfluentwidgets Theme
THEME_MAP = {'跟随系统': Theme.AUTO, '深色': Theme.DARK, '浅色': Theme.LIGHT}

class ResizeEdge(QWidget):
    """窗口边缘透明命中区：固定方向光标，左键交给系统执行拖动缩放。"""

    def __init__(self, parent, edges, cursor):
        super().__init__(parent)
        self.edges = edges
        self.setMouseTracking(True)
        self.setCursor(cursor)
        self.setToolTip('按住左键拖动调整窗口大小')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if not window.isMaximized() and not window.isFullScreen():
                if window.windowHandle().startSystemResize(self.edges):
                    event.accept()
                    return
        super().mousePressEvent(event)


class CompactNavigationBar(NavigationBar):
    """40 像素图标按钮，页面名称通过悬停提示和无障碍名称保留。"""

    def insertItem(self, index, routeKey, icon, text, onClick=None, selectable=True,
                   selectedIcon=None, position=NavigationItemPosition.TOP):
        if routeKey in self.items:
            return self.items[routeKey]
        button = NavigationPushButton(icon, text, selectable, self)
        button.setToolTip(text)
        button.setAccessibleName(text)
        self.insertWidget(index, routeKey, button, onClick, position)
        return button


class ToolbarActionButton(PushButton):
    """文字收起时居中绘制图标，避免 Fluent 文本按钮的最小宽度偏移。"""

    def _drawIcon(self, icon, painter, rect, *args):
        if not self.text():
            rect = QRectF((self.width() - rect.width()) / 2,
                          (self.height() - rect.height()) / 2, rect.width(), rect.height())
        super()._drawIcon(icon, painter, rect, *args)


class _TestSignals(QObject):
    """连接测试按钮：后台线程 -> GUI 主线程 的信号（跨线程安全）。"""

    finished = pyqtSignal(bool)  # True=测试结束，恢复按钮可用


class _RecoverSignals(QObject):
    """手动重启按钮：后台线程 -> GUI 主线程 的信号（跨线程安全）。"""

    finished = pyqtSignal(bool)  # True=恢复成功（宠物主页已打开），拉起调度器时跳过 opener


class _FocusOutPlainTextEdit(PlainTextEdit):
    """失焦时触发保存回调的多行文本框（QPlainTextEdit 没有 editingFinished）。"""

    def __init__(self, on_focus_out):
        super().__init__()
        self._on_focus_out = on_focus_out

    def focusOutEvent(self, event):
        self._on_focus_out()
        super().focusOutEvent(event)


class LogView(PlainTextEdit):
    """日志区：纯文本保证大量日志下的性能；其中的 http(s) 链接可点击，
    单击直接用浏览器打开（悬停显示手型光标）。"""

    # 行内 URL 匹配：排除中文标点/引号/括号结尾（日志里链接常跟"下载：xxx。"）
    _URL_RE = re.compile(r'https?://[^\s<>"\'），。；！？）]+')

    def __init__(self, readOnly: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.setReadOnly(readOnly)  # fluent PlainTextEdit 构造不收 readOnly 关键字
        self.setMouseTracking(True)

    def _url_at(self, pos) -> str | None:
        cursor = self.cursorForPosition(pos)
        col = cursor.positionInBlock()
        for m in self._URL_RE.finditer(cursor.block().text()):
            if m.start() <= col < m.end():
                return m.group(0)
        return None

    def mouseReleaseEvent(self, event):
        # 拖选文本时不触发打开（hasSelection 说明刚在做选择）
        if (event.button() == Qt.MouseButton.LeftButton
                and not self.textCursor().hasSelection()):
            url = self._url_at(event.position().toPoint())
            if url:
                QDesktopServices.openUrl(QUrl(url))
                return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        url = self._url_at(event.position().toPoint())
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if url else Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(event)


class ClickToEdit(QWidget):
    """调度单元格先显示只读值，明确左键点击后才露出编辑控件。"""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.display = LineEdit()
        self.display.setReadOnly(True)
        self.display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.display.setCursor(Qt.CursorShape.ArrowCursor)
        self.display.setText(editor.text())
        self.display.setToolTip('点击后修改；回车或离开输入框完成编辑')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.display)
        layout.addWidget(editor)
        self.setEnabled(editor.isEnabled())
        editor.hide()
        self._finish_on_enter = False
        self._finish_timer = QTimer(self, singleShot=True, interval=0, timeout=self._finish)
        self.display.installEventFilter(self)
        editor.installEventFilter(self)
        for child in editor.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.display:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.display.hide()
                self.editor.show()
                self.editor.setFocus(Qt.FocusReason.MouseFocusReason)
                self.editor.selectAll()
                return True
            if event.type() == QEvent.Type.Wheel:
                event.ignore()
                return True
        else:
            if event.type() == QEvent.Type.FocusOut:
                self._finish_timer.start()
            elif event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._finish_on_enter = True
                self._finish_timer.start()
        return super().eventFilter(obj, event)

    def _finish(self):
        focus = QApplication.focusWidget()
        if not self._finish_on_enter and (focus is self.editor or self.editor.isAncestorOf(focus)):
            return
        self._finish_on_enter = False
        self.display.setText(self.editor.text())
        self.editor.hide()
        self.display.show()


class _NoWheelSpinBox(SpinBox):
    """数字输入框：禁用鼠标滚轮改值（滚轮悬停数字框容易误触加减，防手滑）。

    仅屏蔽滚轮事件，键盘上下键、直接输入等行为不受影响；
    保存由设置页的 valueChanged 触发（数值真的变化才保存，滚轮不再误触发）。
    """

    def wheelEvent(self, event):
        event.ignore()


class _NoWheelTimeEdit(TimeEdit):
    """时间编辑框：禁用鼠标滚轮改值（同 _NoWheelSpinBox，防手滑）。"""

    def wheelEvent(self, event):
        event.ignore()


class _NoInsertEditableComboBox(EditableComboBox):
    """可编辑下拉：手动输入不追加进下拉列表（EditableComboBox 回车默认会把
    输入文本 addItem 进去，设备序列号下拉不需要这个行为）。"""

    def _onReturnPressed(self):
        index = self.findText(self.text())
        if index >= 0 and index != self.currentIndex():
            self.setCurrentIndex(index)


# 设置页面字段：(点路径, 显示名, 类型)
# 类型: 'int' / 'str' / 'bool' / 'text'(多行文本) / 'devices'(adb 设备下拉) / 选项列表
# 设置选项卡：连接/调度引擎/全局规则/告警等全局设置（场景任务相关的在任务选项卡）
SETTING_FIELDS = [
    ('gui.theme', '主题', ['跟随系统', '深色', '浅色']),
    ('adb.path', 'adb 路径', 'str'),
    ('adb.device_serial', '设备序列号', 'devices'),
    ('control.method', '控制方案', ['injectInputEvent', 'minitouch']),
    ('runner.engine', '调度引擎', ['task_queue', 'legacy']),
    ('tasks.failure_interval', '任务失败重试间隔（秒）', 'int'),
    ('schedule.coin_threshold', '金币阈值', 'int'),
    ('schedule.check_interval', '状态检查间隔（秒）', 'int'),
    ('schedule.main_page_checks', '主页面检测次数', 'int'),
    ('schedule.back_method', '返回方式', ['系统返回', '返回图标']),
    ('recover.method', '异常处理方式', ['重启设备', '重启游戏']),
    ('notify.win_toast', '失败告警 Windows 通知', 'bool'),
    ('notify.onepush_config', '失败告警 OnePush 配置', 'text'),
]

# 任务选项卡字段：任务队列顺序 + 各场景任务相关设置
TASK_SETTING_FIELDS = [
    ('tasks.order', '任务执行顺序（> 分隔）', 'str'),
    ('tasks.main_order', '主任务顺序（> 分隔）', 'str'),
    ('school.attribute', '属性点课程', ['力量', '智力', '魅力']),
    ('school.times_per_day', '每天学习次数（0 不限）', 'int'),
    ('schedule.daily_hour_limit', '学习工作时长上限（小时，0 不限）', 'int'),
    ('schedule.encourage_times', '鼓励次数（进行中页面快速点击）', 'int'),
    ('work.location', '打工地点', list(settings_io.WORK_LOCATIONS)),
    ('work.duration', '打工时长选择', ['10分钟', '45分钟', '2小时']),
    ('work.times_per_day', '每天打工次数（0 不限）', 'int'),
    ('work.employ_scroll_limit', '雇佣拖动上限', 'int'),
    ('adventure.times_per_day', '每天冒险次数（0 不冒险）', 'int'),
    ('adventure.start_time', '冒险调度时间', 'str'),
    ('adventure.skip_bad_weather', '冒险跳过"天色不对"', 'bool'),
    ('adventure.batch', '单轮冒险次数', 'int'),
    ('visit.times_per_day', '每天踩踩次数（0 不踩）', 'int'),
    ('visit.start_time', '踩踩调度时间', 'str'),
    ('pk.times_per_day', '每天 PK 次数（0 不 PK）', 'int'),
    ('pk.start_time', 'PK 调度时间', 'str'),
    ('friend_care.enabled', '启用好友护理', 'bool'),
    ('friend_care.time_range', '好友护理时间段', 'str'),
    ('friend_care.friend_name', '护理好友名称', 'str'),
    ('friend_care.method', '护理好友方式', ['一键护理', 'ocr检测']),
    ('friend_care.interval_seconds', '好友护理调度间隔（秒）', 'int'),
    ('hire_friend.enabled', '雇佣好友开关', 'bool'),
    ('hire_friend.time_range', '雇佣好友时间段', 'str'),
    ('hire_friend.interval_seconds', '雇佣好友调度间隔（秒）', 'int'),
    ('hire_friend.friend_name', '雇佣好友名称', 'str'),
    ('hire_friend.times_per_day', '雇佣好友次数（0 不雇佣）', 'int'),
    ('care.method', '护理方式', ['一键护理', 'ocr检测']),
    ('care.energy_threshold', '体力阈值', 'int'),
    ('care.clean_threshold', '清洁阈值', 'int'),
    ('care.interval_seconds', '护理间隔（秒）', 'int'),
    ('employed.action', '被雇佣后处理', ['等到25/75（小于45min）', '等到25/75', '立刻召回']),
    ('employed.enabled', '被雇佣开关', 'bool'),
    ('employed.time_range', '被雇佣时间段', 'str'),
    ('employed.interval_seconds', '被雇佣检查间隔（秒）', 'int'),
]

# 调度选项卡的任务显示名（任务键定义在 src/config.py 的 TASK_KEYS）
SCHEDULE_TASK_NAMES = {'care': '护理', 'adventure': '冒险', 'visit': '踩踩', 'pk': 'PK',
                       'hire_friend': '雇佣好友', 'friend_care': '好友护理',
                       'school': '学习', 'work': '打工'}

# 设置/任务表单的分组卡片标题：按配置键第一段分组（顺序按字段首次出现）
SETTING_GROUP_TITLES = {
    'adb': '连接', 'control': '连接',
    'gui': '界面',
    'recover': '异常恢复',
    'runner': '调度引擎', 'tasks': '任务队列', 'schedule': '全局规则',
    'notify': '告警通知',
    'school': '学习', 'work': '打工', 'adventure': '冒险',
    'visit': '踩踩', 'pk': 'PK',
    'friend_care': '好友护理', 'hire_friend': '雇佣好友',
    'care': '护理', 'employed': '被雇佣',
}


def _scrcpy_title() -> str:
    """本实例唯一的 scrcpy 窗口标题：设备序列号 + 本进程 PID。

    多开时各实例标题互不相同，嵌入查找只匹配自己的窗口，
    不会把别的实例的画面抓到本窗口里（同实例内看门狗重拉标题不变）。
    """
    serial = load_config().adb.device_serial or 'auto'
    return f'{SCRCPY_TITLE_PREFIX}-{serial}-{os.getpid()}'


def _kill_scrcpy_by_marker(marker: str) -> None:
    """结束命令行里包含 marker 的 scrcpy.exe 进程（不影响其他实例/程序）。

    taskkill /IM 会杀掉所有实例的 scrcpy（多开相互影响），这里用 PowerShell CIM
    按命令行精确过滤；marker 经环境变量传入，避免引号/通配符转义问题。
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='scrcpy.exe'\" "
        "| Where-Object { $_.CommandLine -and $_.CommandLine.Contains($env:QQPET_SCRCPY_MARKER) } "
        "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    env = dict(os.environ, QQPET_SCRCPY_MARKER=marker)
    subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
        capture_output=True, timeout=20, creationflags=_NO_WINDOW, env=env,
    )


def kill_our_scrcpy(proc: subprocess.Popen | None = None) -> None:
    """结束本实例的 scrcpy：跟踪的进程 + 命令行带本实例唯一标记的残留进程。

    多开时只清自己的，不碰别的实例。
    """
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
            return  # 已跟踪进程退出，无需每次启动 PowerShell/WMI 扫描。
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
            return
    try:
        _kill_scrcpy_by_marker(_scrcpy_title())
    except Exception:
        log('结束本实例 scrcpy 残留进程失败（可忽略）')


def kill_previous_scrcpy() -> None:
    """启动时清理本设备上次崩溃遗留的 scrcpy（还占着镜像/关屏）。

    只按"设备序列号"前缀匹配，不碰其他设备上的实例；未指定序列号时
    无法安全区分，跳过（多开安全优先）。
    """
    serial = load_config().adb.device_serial
    if not serial:
        return
    try:
        _kill_scrcpy_by_marker(f'{SCRCPY_TITLE_PREFIX}-{serial}-')
    except Exception:
        log('清理本设备遗留 scrcpy 失败（可忽略）')


def _scrcpy_port(serial: str) -> str:
    """本实例 scrcpy 客户端监听端口范围（按设备序列号稳定在 28200 起，8 个连续端口）。

    scrcpy 默认端口范围 27183:27199，取第一个能绑定的；但 Windows 下即使别的
    scrcpy 已占用 27183，绑定也能"成功"（SO_REUSEADDR 语义），结果多个实例的
    scrcpy 都监听同一端口，各设备经 adb reverse 回连 127.0.0.1:27183 时被
    投递到错误的进程——双开同时打开镜像时画面串台（两个窗口同一画面）或
    "Server connection failed"立刻退出；一个个开时后绑定的拿到后到的连接，
    恰好不错位，所以难复现。按序列号分配固定端口后各实例隧道互不相干。
    """
    port = 28200 + zlib.crc32(serial.encode('utf-8')) % 3000
    return f'{port}:{port + 7}'


def window_is_foreground(window) -> bool:
    """嵌入的 scrcpy 属于另一个进程，Qt 活跃状态不足以判断前后台。"""
    if sys.platform == 'win32' and hasattr(window, 'winId'):
        try:
            foreground = win32gui.GetForegroundWindow()
            if not foreground:
                return window.isActiveWindow()
            try:
                _, foreground_pid = win32process.GetWindowThreadProcessId(foreground)
            except Exception:
                foreground_pid = None
            if foreground_pid == os.getpid():
                return True
            # SDL can retain its own root/owner after cross-process SetParent.
            mirror = getattr(window, '_scrcpy_proc', None)
            if mirror is not None and mirror.poll() is None and foreground_pid == mirror.pid:
                return True
            # GA_ROOTOWNER 同时覆盖 SetParent 的原生子窗口及本窗口弹出的对话框。
            return bool(foreground and win32gui.GetAncestor(foreground, 3)
                        == win32gui.GetAncestor(int(window.winId()), 3))
        except Exception:
            pass
    return window.isActiveWindow()


def start_scrcpy() -> subprocess.Popen | None:
    """以无边框、关屏、固定标题启动 scrcpy，返回进程。"""
    if not SCRCPY.is_file():
        log(f'未找到 {SCRCPY}，跳过 scrcpy 启动')
        return None
    cmd = [str(SCRCPY)]
    serial = load_config().adb.device_serial
    if serial:  # 指定设备序列号
        cmd += ['-s', serial]
    cmd += ['--no-audio', '--max-size=1024', '--max-fps=15', '--video-bit-rate=2M']
    # 镜像仅用于预览；自动化仍使用原始截图，不受预览尺寸/帧率影响。
    cmd.append('--turn-screen-off')
    cmd += ['--window-borderless', '--stay-awake',
            f'--port={_scrcpy_port(serial or "")}',  # 多开防端口撞车串台
            f'--window-title={_scrcpy_title()}',
            # 先放到屏幕外，嵌入容器时再移回来，避免窗口先弹出再嵌入的闪烁
            '--window-x=-2000', '--window-y=-2000']
    flags = '--no-audio' + ' --turn-screen-off' + ' --window-borderless'
    log(f'启动 scrcpy（{flags}'
        + (f'，设备 {serial}）...' if serial else '）...'))
    proc = subprocess.Popen(
        cmd,
        cwd=str(SCRCPY.parent),  # scrcpy 需要同目录的 scrcpy-server 等文件
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    time.sleep(1)
    if proc.poll() is not None:
        log('警告: scrcpy 启动后立刻退出了，请检查设备连接')
        return None
    return proc


def start_scrcpy_screen_off() -> subprocess.Popen | None:
    """无头 scrcpy 关闭设备屏幕：--turn-screen-off + 保持唤醒，不传画面/音频/不开窗口。

    画面镜像关闭后用它把设备屏幕真正关掉（比亮度 0 更彻底）；
    --stay-awake 让设备保持唤醒（渲染管线不断，OCR/自动化照常），
    --no-window 不显示任何窗口。返回进程；失败返回 None（屏幕保持原状）。
    """
    if not SCRCPY.is_file():
        log(f'未找到 {SCRCPY}，跳过屏幕关闭')
        return None
    cmd = [str(SCRCPY), '--turn-screen-off', '--no-video', '--no-audio',
           '--stay-awake', '--no-window']
    serial = load_config().adb.device_serial
    if serial:
        cmd += ['-s', serial]
    # 无头关屏 scrcpy 同样占用监听端口，必须按实例区分（理由见 _scrcpy_port）
    cmd.append(f'--port={_scrcpy_port(serial or "")}')
    log('启动 scrcpy 关闭屏幕（--turn-screen-off --no-video --no-audio '
        '--stay-awake --no-window）...')
    proc = subprocess.Popen(
        cmd,
        cwd=str(SCRCPY.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    time.sleep(1)
    if proc.poll() is not None:
        log('警告: 屏幕关闭 scrcpy 启动后立刻退出了')
        return None
    return proc


def find_scrcpy_hwnd(proc: subprocess.Popen | None = None) -> int | None:
    """按本实例唯一标题查找 scrcpy 窗口句柄。

    proc 传本实例跟踪的 scrcpy 进程时，再按进程 PID 过滤：
    重启瞬间旧窗口可能还没销毁（同标题），或别的实例窗口标题撞上，
    只有属于自己进程的窗口才会被嵌入。
    """
    title = _scrcpy_title()
    want_pid = proc.pid if proc is not None and proc.poll() is None else None
    found = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetWindowText(hwnd) != title:
            return
        if want_pid is not None:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid != want_pid:
                return
        found.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None


class ScrcpyContainer(QWidget):
    """scrcpy 窗口的嵌入容器，按手机屏幕比例等比适配并居中。

    比例只在 _fit 中处理，不把画面比例传成顶层窗口的尺寸约束。
    设备未连接或比例读取失败时按 (9, 16) 兜底。
    """

    def __init__(self):
        super().__init__()
        self._hwnd: int | None = None
        self._aspect: tuple[int, int] | None = None  # 手机屏幕物理像素 (宽, 高)
        self._last_geometry = None
        # 普通 QWidget 子类要开 WA_StyledBackground，样式表背景才会真正绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 未嵌入时跟随主题背景（透出下层卡片色），不显示死黑一块；
        # 嵌入后画面按真实比例正好铺满，无需黑色留白
        self.setStyleSheet('background: transparent; border-radius: 8px;')
        self.setMinimumWidth(160)

    def sizeHint(self) -> QSize:
        return QSize(360, 640)  # 9:16 竖屏

    def hasHeightForWidth(self) -> bool:
        # 原生 Windows 会把顶层布局的 height-for-width 当作高度约束，
        # 导致用户拖动后窗口又弹回。画面比例只由 _fit 内部留白/缩放保证。
        return False

    def heightForWidth(self, width: int) -> int:
        return width * 16 // 9  # 9:16 竖屏

    def set_hwnd(self, hwnd: int | None) -> None:
        self._hwnd = hwnd
        self._last_geometry = None

    def embed(self, hwnd: int, aspect: tuple[int, int] | None = None) -> None:
        self.set_hwnd(hwnd)
        win32gui.SetParent(hwnd, int(self.winId()))
        # 缩放前先取 scrcpy 窗口客户区真实尺寸作为嵌入比例（客户区 = 视频画面大小，
        # 自适应任何设备与 --max-size 设置）；device_aspect 的设备物理分辨率比例
        # 可能和实际窗口不一致（如 720x1280 等比缩放窗口 vs 1080x2400 物理屏），
        # 用它会留出两侧黑边
        try:
            _, _, real_w, real_h = win32gui.GetClientRect(hwnd)
            if real_w > 32 and real_h > 32:
                aspect = (real_w, real_h)
        except Exception:
            pass
        self._aspect = aspect
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        win32gui.SetWindowLong(
            hwnd, win32con.GWL_STYLE,
            (style & ~(win32con.WS_POPUP | win32con.WS_CAPTION | win32con.WS_THICKFRAME
                       | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX))
            | win32con.WS_CHILD,
        )
        win32gui.SetWindowPos(
            hwnd, None, 0, 0, self.width(), self.height(),
            win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW,
        )
        self._fit()
        log('scrcpy 窗口已嵌入')

    def _fit(self) -> None:
        """把 scrcpy 窗口等比缩放到容器内最大并居中，避免内部留黑边。"""
        if not self._hwnd:
            return
        cw, ch = self.width(), self.height()
        # 设备比例未知（未连接/读取失败）时按 9:16 竖屏兜底
        aw, ah = self._aspect if self._aspect and all(self._aspect) else (9, 16)
        scale = min(cw / aw, ch / ah)
        w, h = int(aw * scale), int(ah * scale)
        x, y = (cw - w) // 2, (ch - h) // 2
        geometry = (x, y, w, h)
        if geometry != self._last_geometry:
            win32gui.SetWindowPos(self._hwnd, None, x, y, w, h,
                                 win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
            self._last_geometry = geometry

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()


def device_aspect() -> tuple[int, int] | None:
    """读取手机屏幕物理像素 (宽, 高)，用于等比嵌入；失败返回 None。"""
    try:
        from src.adb.device import Device
        from src.config import find_adb, load_config

        cfg = load_config()
        dev = Device(find_adb(cfg.adb.path), cfg.adb.device_serial)
        return dev.screen_size()
    except Exception:
        return None


def _format_remaining(seconds: float) -> str:
    """剩余秒数 -> 人性化倒计时：>1 天 xx天xx小时xx分钟，>1 小时 xx小时xx分钟，
    >1 分钟 xx分钟xx秒，否则 xx秒。"""
    secs = max(0, int(seconds))
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    minutes, secs = divmod(secs, 60)
    if days:
        return f'{days}天{hours}小时{minutes}分钟'
    if hours:
        return f'{hours}小时{minutes}分钟'
    if minutes:
        return f'{minutes}分钟{secs}秒'
    return f'{secs}秒'


class CompactCardWidget(HeaderCardWidget):
    """紧凑版 HeaderCardWidget：标题栏 48→34、内容边距 24→(16,10,16,12)。

    原版卡片 chrome 占高 ~96px，分组卡片多了一页放不下几组设置；
    所有分组卡片（主页状态/统计/日志、设置/任务页）统一用这个。
    """

    def _postInit(self):
        self.headerView.setFixedHeight(34)
        self.headerLayout.setContentsMargins(16, 0, 12, 0)
        self.viewLayout.setContentsMargins(16, 10, 16, 12)


class TwoColumnCardsPanel(QWidget):
    """分组卡片两列容器：左右两个竖列，add_card 按两列累计高度塞到较矮的一列。

    每列内部紧密排列（spacing 12）、列尾 stretch 顶格对齐，卡片高度就是内容高度，
    同列卡片之间不会出现 FlowLayout"行高=该行最高卡片"造成的大空白；
    两列 stretch 相同保证卡片等宽。设置/任务页共用。
    注意：分组卡片创建时还是空的（字段后填），高度平衡要等字段填完调 finalize()。
    """

    def __init__(self):
        super().__init__()
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        self._columns: list = []
        self._heights = [0, 0]  # 两列累计高度（sizeHint 估算），平衡用
        self._pending: list = []  # finalize 前添加的卡片
        self._finalized = False
        for _ in range(2):
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(12)
            col.addStretch(1)  # 列尾顶格：卡片永远从列头紧密排起
            box.addLayout(col, 1)
            self._columns.append(col)

    def add_card(self, card: QWidget) -> None:
        if self._finalized:
            self._place(card)  # 此时字段已填完，sizeHint 可信
        else:
            self._pending.append(card)

    def finalize(self) -> None:
        """字段全部填完后调用：按 sizeHint 高度把卡片平衡进两列。"""
        for card in self._pending:
            self._place(card)
        self._pending.clear()
        self._finalized = True

    def _place(self, card: QWidget) -> None:
        idx = 0 if self._heights[0] <= self._heights[1] else 1
        col = self._columns[idx]
        col.insertWidget(col.count() - 1, card)  # 插到列尾 stretch 之前
        self._heights[idx] += card.sizeHint().height() + 12


class MainWindow(MSFluentWindow):
    # 检查更新结果回投 GUI 线程：(manual, UpdateCheckResult)
    _sig_update_result = pyqtSignal(object)

    def updateFrameless(self):
        super().updateFrameless()
        self._remove_native_caption()

    def _remove_native_caption(self):
        # qframelesswindow 为窗口动画加回 WS_CAPTION；系统移动窗口时可能
        # 绘出第二条原生标题栏。只移除 caption，保留缩放框和最大/最小化能力。
        if sys.platform != 'win32' or QApplication.platformName() != 'windows':
            return
        hwnd = int(self.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        if style & win32con.WS_CAPTION:
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style & ~win32con.WS_CAPTION)
            win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                 win32con.SWP_NOMOVE | win32con.SWP_NOSIZE |
                                 win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE |
                                 win32con.SWP_FRAMECHANGED)

    def showEvent(self, event):
        super().showEvent(event)
        self._remove_native_caption()

    def __init__(self):
        super().__init__()
        self._next_profile = None
        old_navigation = self.navigationInterface
        self.hBoxLayout.removeWidget(old_navigation)
        old_navigation.hide()
        old_navigation.deleteLater()
        self.navigationInterface = CompactNavigationBar(self)
        self.navigationInterface.setFixedWidth(48)
        self.hBoxLayout.insertWidget(0, self.navigationInterface)
        self.titleBar.setFixedHeight(32)
        self.titleBar.closeBtn.clicked.disconnect()
        self.titleBar.closeBtn.clicked.connect(self._on_title_close)
        self.hBoxLayout.setContentsMargins(0, 32, 0, 0)
        self.setWindowTitle(f'QQ 宠物自动化助手 v{APP_VERSION}')
        self.resize(1280, 820)
        self.setMinimumSize(800, 500)
        self.setResizeEnabled(True)
        self.BORDER_WIDTH = 8  # 无边框窗口的边缘拖动区域更容易命中。
        self._size_grip = QSizeGrip(self)
        self._size_grip.setToolTip('拖动调整窗口大小')
        edge, cursor = Qt.Edge, Qt.CursorShape
        self._resize_edges = [
            ResizeEdge(self, edge.LeftEdge, cursor.SizeHorCursor),
            ResizeEdge(self, edge.RightEdge, cursor.SizeHorCursor),
            ResizeEdge(self, edge.TopEdge, cursor.SizeVerCursor),
            ResizeEdge(self, edge.BottomEdge, cursor.SizeVerCursor),
            ResizeEdge(self, edge.LeftEdge | edge.TopEdge, cursor.SizeFDiagCursor),
            ResizeEdge(self, edge.RightEdge | edge.TopEdge, cursor.SizeBDiagCursor),
            ResizeEdge(self, edge.LeftEdge | edge.BottomEdge, cursor.SizeBDiagCursor),
            ResizeEdge(self, edge.RightEdge | edge.BottomEdge, cursor.SizeFDiagCursor),
        ]

        self.scrcpy_view = ScrcpyContainer()
        self.log_view = LogView(readOnly=True)
        self.log_view.setMaximumBlockCount(LOG_MAX_LINES)

        # 设置/任务页的表单控件注册表（加载/保存共用，见 _build_settings_form）
        self._setting_widgets: dict = {}
        # 护理方式选"一键护理"时体力/清洁阈值用不上，隐藏对应表单行（label + 控件）
        self._care_threshold_rows: dict = {}
        # 主页宠物状态卡片：缓存字段 -> 数值标签（每秒刷新，见 _refresh_stats）
        self._status_values: dict = {}

        self._build_control_widgets()
        self._install_toolbar()
        self._init_navigation()

        self._scrcpy_proc: subprocess.Popen | None = None
        self._background_mirror_paused = False
        self._runner_proc: subprocess.Popen | None = None
        self._runner_started_at: float | None = None  # 调度器启动时刻（monotonic），主页显示运行时间用
        self._recovering = False  # 手动重启进行中：期间开始/停止按钮联动禁用

        # 日志：本进程监听器 + 调度子进程 stdout -> 队列 -> 定时器刷到界面
        self._log_queue: queue.Queue = queue.Queue()
        add_log_listener(self._log_queue.put)
        self._log_timer = QTimer(self, timeout=self._drain_logs)
        self._log_timer.start(100)
        # 当日统计：每秒从进度文件刷新一次（状态条倒计时需要秒级刷新）
        self._stats_timer = QTimer(self, timeout=self._refresh_stats)
        self._stats_timer.start(1000)
        self._refresh_stats()

        # 检查更新：启动后自动查一次，之后每 6 小时一次；设置页可手动触发
        self._update_checking = False
        self._sig_update_result.connect(self._on_update_result)
        self._update_timer = QTimer(
            self, timeout=lambda: self._start_update_check(manual=False))
        self._update_timer.start(UPDATE_CHECK_INTERVAL_MS)
        QTimer.singleShot(5000, lambda: self._start_update_check(manual=False))

        self._embed_tries = 0
        self._embed_fail_logged = False
        self._embed_timer = QTimer(self, timeout=self._try_embed)
        # scrcpy 看门狗：设备重启/掉线后 scrcpy 进程会退出，自动重拉并重嵌入
        self._scrcpy_retry_at = 0.0
        self._scrcpy_watchdog = QTimer(self, timeout=self._check_scrcpy)
        self._scrcpy_watchdog.start(SCRCPY_WATCHDOG_MS)
        # 画面镜像关闭时关屏一次：GUI 侧 adb Device（懒加载复用）
        self._adb_dev = None
        self._adb_dev_key = None
        self._screen_off_proc: subprocess.Popen | None = None  # 无头关屏 scrcpy
        # 配置保存后重启调度器的防抖定时器
        self._restart_timer = QTimer(self, singleShot=True, interval=1500,
                                     timeout=self._restart_runner)
        # 连接测试：GUI 侧独立 u2 连接（懒加载复用，adb 配置变化自动重建）
        self._test_dev = None
        self._test_dev_key = None
        self._test_lock = threading.Lock()
        # 后台测试线程完成 -> 主线程恢复按钮（QTimer.singleShot 在非 Qt 线程不可靠，
        # 用信号跨线程投递）
        self._test_signals = _TestSignals()
        self._test_signals.finished.connect(self._set_test_btn_enabled)
        # 手动重启完成 -> 主线程恢复按钮/拉回调度器（同连接测试的信号模式）
        self._recover_signals = _RecoverSignals()
        self._recover_signals.finished.connect(self._on_recover_finished)

        QTimer.singleShot(0, self._start_all)

    # ---- 界面构建 ----

    def _build_control_widgets(self) -> None:
        """顶部工具栏上的按钮/开关（处理器与各状态字段与原布局一致）。"""
        # 开始 = 子进程启动调度器，停止 = 立即结束调度器进程
        self.btn_start = PrimaryPushButton('开始', self, FIF.PLAY)
        self.btn_stop = PushButton('停止', self, FIF.CLOSE)
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_runner)
        self.btn_stop.clicked.connect(self.stop_runner)
        # 画面镜像开关：开=启动 scrcpy 并看门狗自动重连，关=结束进程且不再自动拉起。
        # 开关状态持久化到 gui.mirror，下次启动保持。
        # 注意先 setChecked 再连接：SwitchButton 的 checkedChanged 在 setChecked 时就触发
        self.btn_scrcpy = SwitchButton(self)
        self.btn_scrcpy.setOnText('开')
        self.btn_scrcpy.setOffText('关')
        self.btn_scrcpy.setChecked(load_config().gui.mirror)
        self.btn_scrcpy.checkedChanged.connect(self._toggle_scrcpy)
        # 连接测试：u2 截图 + OCR 识别 + 控件树拉取 耗时（后台线程执行，结果在日志页）
        self._btn_connect_test = ToolbarActionButton('连接测试', self, FIF.LINK)
        self._btn_connect_test.clicked.connect(self._test_connect)
        # 手动重启：按 recover.method 配置执行一次异常恢复（重启设备/重启游戏回宠物页）
        self._btn_manual_recover = ToolbarActionButton('手动重启', self, FIF.SYNC)
        self._btn_manual_recover.clicked.connect(self._manual_recover)

    def _build_toolbar(self) -> CardWidget:
        """顶部全局工具栏：开始/停止 + 画面镜像开关 + 连接测试/手动重启，右侧运行时间。"""
        card = CardWidget(self)
        self._toolbar_card = card
        self._toolbar_compact = None
        card.setFixedHeight(56)
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addSpacing(6)
        row.addWidget(BodyLabel('画面镜像'))
        row.addWidget(self.btn_scrcpy)
        row.addSpacing(6)
        row.addWidget(self._btn_connect_test)
        row.addWidget(self._btn_manual_recover)
        row.addStretch(1)
        self._runtime_label = BodyLabel('运行时间 0小时0分钟')
        row.addWidget(self._runtime_label)
        self._btn_connect_test.setToolTip('连接测试：检查设备连接和识别')
        self._btn_manual_recover.setToolTip('手动重启：恢复 QQ 宠物页面')
        return card

    def _update_chrome_layout(self):
        card = getattr(self, '_toolbar_card', None)
        if card is None:
            return
        compact = card.width() < 900
        if compact == self._toolbar_compact:
            return
        self._toolbar_compact = compact
        # 小窗口保留主按钮文字，辅助操作用原图标和提示，避免工具栏变成两行。
        self._btn_connect_test.setText('' if compact else '连接测试')
        self._btn_manual_recover.setText('' if compact else '手动重启')
        for button in (self._btn_connect_test, self._btn_manual_recover):
            button.setMinimumWidth(36 if compact else 0)
            button.setMaximumWidth(36 if compact else 16777215)

    def _install_toolbar(self) -> None:
        """把 stackedWidget 包进右侧容器（上工具栏、下页面堆栈），
        工具栏固定在窗口内容区顶部，切换导航页不受影响。"""
        right = QWidget()
        box = QVBoxLayout(right)
        box.setContentsMargins(4, 8, 16, 0)
        box.setSpacing(10)
        box.addWidget(self._build_toolbar())
        self.hBoxLayout.removeWidget(self.stackedWidget)
        box.addWidget(self.stackedWidget, 1)
        self.hBoxLayout.addWidget(right, 1)

    def _init_navigation(self) -> None:
        """导航按钮统一在顶部排列，尺寸变化时保持相同位置和间距。"""
        self.navigationInterface.vBoxLayout.setContentsMargins(0, 8, 0, 12)
        self.navigationInterface.topLayout.setContentsMargins(4, 0, 4, 0)
        self.navigationInterface.setIndicatorAnimationEnabled(False)
        self.addSubInterface(self._build_home_page(), FIF.HOME, '主页')
        self.addSubInterface(self._build_schedule_page(), FIF.CALENDAR, '调度')
        self.stats_panel = StatsPanel()
        self.stats_panel.setObjectName('statsPage')
        self.addSubInterface(self._wrap_page(self.stats_panel), FIF.HISTORY, '统计')
        self.addSubInterface(self._build_tasks_page(), FIF.TILES, '任务')
        self.addSubInterface(self._build_settings_page(), FIF.SETTING, '设置')
        self.stackedWidget.currentChanged.connect(self._on_tab_changed)

    @staticmethod
    def _wrap_page(content: QWidget) -> QWidget:
        """给导航页内容加统一外边距（左右已由工具栏容器留白，页面只管上下）。"""
        page = content
        page.setContentsMargins(0, 4, 0, 12)
        return page

    def _build_home_page(self) -> QWidget:
        """主页：左侧 scrcpy 画面卡片（9:16 竖屏等比嵌入，宽度随高度自适应），
        右侧 上=宠物状态、中=今日统计、下=日志（吃掉剩余空间）。"""
        page = QWidget()
        page.setObjectName('homePage')
        self._home_page = page
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 12)
        layout.setSpacing(16)

        self._screen_card = SimpleCardWidget()
        screen_layout = QVBoxLayout(self._screen_card)
        screen_layout.setContentsMargins(4, 4, 4, 4)
        screen_layout.addWidget(self.scrcpy_view)
        # 画面卡宽度 = 高度 × 画面比例（_fit_screen_card，窗口缩放/嵌入后重算），
        # 不用 QSplitter：把手在深色主题下会渲染成一条白色竖条，且宽度本就由
        # 高度推导，拖动没有意义
        layout.addWidget(self._screen_card)

        side = QWidget()
        side.setMinimumWidth(400)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(12)
        # 状态/任务队列/今日卡垂直方向 Maximum：紧贴内容高度，多余空间全给日志卡
        # （否则侧栏多余高度把它们撑高，卡片内容垂直居中、网格与状态行之间空出大块）
        status_card = self._build_status_card()
        status_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        queue_card = self._build_queue_card()
        queue_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        today_card = self._build_today_card()
        today_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        side_layout.addWidget(self._build_profile_card())
        side_layout.addWidget(status_card)
        side_layout.addWidget(queue_card)
        side_layout.addWidget(today_card)
        log_card = self._build_log_card()
        log_card.setMinimumHeight(160)
        side_layout.addWidget(log_card, 1)
        self._home_scroll = ScrollArea()
        self._home_scroll.setWidgetResizable(True)
        self._home_scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        self._home_scroll.setWidget(side)
        layout.addWidget(self._home_scroll, 1)
        return page

    def _build_profile_card(self):
        from src.profiles import ProfileStore
        self._profiles = ProfileStore(APP_ROOT)
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        row = QHBoxLayout()
        row.addWidget(StrongBodyLabel('配置'))
        self.profile_combo = ComboBox()
        self._fill_profiles()
        row.addWidget(self.profile_combo, 1)
        for title, action in [('新建', self._new_profile), ('重命名', self._rename_profile),
                              ('切换', self._switch_profile)]:
            button = PushButton(title)
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        active_name = self._profiles.read()['profiles'][PROFILE_ID]
        self.profile_active_label = CaptionLabel(f'当前：{active_name} · 停止调度后切换，界面将重新打开')
        layout.addWidget(self.profile_active_label)
        return card

    def _fill_profiles(self, selected=PROFILE_ID):
        self.profile_combo.clear()
        for key, name in self._profiles.read()['profiles'].items():
            self.profile_combo.addItem(name, userData=key)
        self.profile_combo.setCurrentIndex(self.profile_combo.findData(selected))

    def _profile_error(self, error):
        MessageBox('配置管理', str(error), self).exec()

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, '新建配置', '自定义名称（复制当前设置，统计从零开始）：')
        if ok:
            try:
                key = self._profiles.create(name, PROFILE_ID)
                self._fill_profiles(key)
            except Exception as exc:
                self._profile_error(exc)

    def _rename_profile(self):
        key = self.profile_combo.currentData()
        name, ok = QInputDialog.getText(self, '重命名配置', '配置名称：', text=self.profile_combo.currentText())
        if ok:
            try:
                self._profiles.rename(key, name)
                self._fill_profiles(key)
                if key == PROFILE_ID:
                    self.profile_active_label.setText(f'当前：{name.strip()} · 停止调度后切换，界面将重新打开')
            except Exception as exc:
                self._profile_error(exc)

    def _switch_profile(self):
        key = self.profile_combo.currentData()
        if key == PROFILE_ID:
            return
        if self._recovering or self._test_lock.locked() or (self._runner_proc and self._runner_proc.poll() is None):
            self._profile_error('请先停止调度，并等待连接测试或手动重启完成，再切换配置。')
            return
        try:
            self._profiles.select(key)
            self._restart_timer.stop()
            self._next_profile = key
            self.close()
        except Exception as exc:
            self._profile_error(exc)

    def _fit_screen_card(self) -> None:
        """把画面卡宽度收成 高度×画面比例（消除两侧黑边），窗口缩放和嵌入后调用。"""
        card = getattr(self, '_screen_card', None)
        if card is None:
            return
        h = card.height() - 8  # 卡片内边距 4×2
        if h <= 0:
            return
        aw, ah = self.scrcpy_view._aspect or (9, 16)
        if not aw or not ah:
            aw, ah = 9, 16
        # 同时受可用宽度限制，不能由高度反推的固定宽度把主窗口撑回去。
        available = max(180, self._home_page.width() - 440)
        target = min(available, max(180, int(h * aw / ah) + 8))
        if card.width() != target:
            card.setFixedWidth(target)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 等布局算完再按新高度收宽度（resizeEvent 触发时 height 还是旧值）
        QTimer.singleShot(0, self._fit_screen_card)
        QTimer.singleShot(0, self._update_chrome_layout)
        grip = getattr(self, '_size_grip', None)
        if grip is not None:
            grip.setVisible(not self.isMaximized() and not self.isFullScreen())
            grip.resize(20, 20)
            grip.move(self.width() - 22, self.height() - 22)
            grip.raise_()
        self._position_resize_edges()

    def _position_resize_edges(self):
        handles = getattr(self, '_resize_edges', ())
        w, h, b = self.width(), self.height(), self.BORDER_WIDTH
        rects = [(0,b,b,h-2*b), (w-b,b,b,h-2*b), (b,0,w-2*b,b),
                 (b,h-b,w-2*b,b), (0,0,b,b), (w-b,0,b,b),
                 (0,h-b,b,b), (w-b,h-b,b,b)]
        for handle, rect in zip(handles, rects):
            handle.setGeometry(*rect)
            handle.setVisible(not self.isMaximized() and not self.isFullScreen())
            handle.raise_()

    def nativeEvent(self, eventType, message):
        # Windows 非客户区命中可能先于 Qt 子控件收到事件，显式设置对应系统光标。
        if sys.platform == 'win32':
            from ctypes.wintypes import MSG
            msg = MSG.from_address(int(message))
            if (msg.message == win32con.WM_SETCURSOR and not self.isMaximized()
                    and not self.isFullScreen()):
                shapes = {
                    win32con.HTLEFT: win32con.IDC_SIZEWE,
                    win32con.HTRIGHT: win32con.IDC_SIZEWE,
                    win32con.HTTOP: win32con.IDC_SIZENS,
                    win32con.HTBOTTOM: win32con.IDC_SIZENS,
                    win32con.HTTOPLEFT: win32con.IDC_SIZENWSE,
                    win32con.HTBOTTOMRIGHT: win32con.IDC_SIZENWSE,
                    win32con.HTTOPRIGHT: win32con.IDC_SIZENESW,
                    win32con.HTBOTTOMLEFT: win32con.IDC_SIZENESW,
                }
                shape = shapes.get(msg.lParam & 0xffff)
                if shape:
                    win32gui.SetCursor(win32gui.LoadCursor(None, shape))
                    return True, 1
        return super().nativeEvent(eventType, message)

    def _build_status_card(self) -> HeaderCardWidget:
        """宠物状态卡片：体力/清洁/心情/金币/饼干/香皂 横排一行均匀分布。"""
        card = CompactCardWidget()
        card.setTitle('宠物状态')
        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for key, label in STATUS_FIELDS:
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            cell_layout.addWidget(CaptionLabel(label))
            value = StrongBodyLabel('-')
            cell_layout.addWidget(value)
            self._status_values[key] = value
            layout.addWidget(cell, 1)  # 等宽均分整行
        card.viewLayout.addWidget(body)
        return card

    def _build_queue_card(self) -> HeaderCardWidget:
        """任务队列卡片：当前任务/下一任务/待执行/等待中 横排一行均匀分布。

        调度器运行时读 runs/queue_status.json 的精确状态，未运行按配置推算
        （与调度页"下次执行"列共用 _predict_next），见 _refresh_queue_card。
        """
        card = CompactCardWidget()
        card.setTitle('任务队列')
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._queue_values = {}
        for key, label in (('current', '当前任务'), ('next', '下一任务'),
                           ('ready', '待执行'), ('waiting', '等待中')):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            cell_layout.addWidget(CaptionLabel(label))
            value = StrongBodyLabel('-')
            cell_layout.addWidget(value)
            self._queue_values[key] = value
            row.addWidget(cell, 1)  # 四格等宽均分
        card.viewLayout.addWidget(body)
        return card

    def _build_today_card(self) -> HeaderCardWidget:
        """今日统计卡片：两行网格均匀分布（每秒从进度文件刷新）。

        上行：学习(h)/工作(h)/学习/打工/冒险；下行：踩踩/经验日常/PK/被雇佣。
        """
        card = CompactCardWidget()
        card.setTitle('今日统计')
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self._today_values = {}
        fields = (('study_h', '学习(h)'), ('work_h', '工作(h)'),
                  ('学习', '学习'), ('打工', '打工'), ('冒险', '冒险'),
                  ('踩踩', '踩踩'), ('经验日常', '经验日常'), ('PK', 'PK'), ('被雇佣', '被雇佣'))
        for i, (key, label) in enumerate(fields):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            cell_layout.addWidget(CaptionLabel(label))
            value = StrongBodyLabel('-')
            cell_layout.addWidget(value)
            self._today_values[key] = value
            grid.addWidget(cell, i // 5, i % 5)
        for col in range(5):
            grid.setColumnStretch(col, 1)  # 5 列等宽均分
        card.viewLayout.addWidget(body)
        return card

    def _build_log_card(self) -> HeaderCardWidget:
        """主页日志卡片：调度器与本进程日志实时显示（链接可点击），吃掉右侧剩余空间。"""
        card = CompactCardWidget()
        card.setTitle('日志')
        # viewLayout 是 QHBoxLayout，日志撑满
        card.viewLayout.addWidget(self.log_view, 1)
        return card

    # ---- 启动流程 ----

    def _start_all(self) -> None:
        # Qt 槽里未捕获的异常会直接 abort 进程（无 traceback 的"闪退"），
        # 启动失败记日志并继续，调度器仍可手动开始
        try:
            kill_previous_scrcpy()
            if self.btn_scrcpy.isChecked():
                self._scrcpy_proc = start_scrcpy()
            else:
                log('画面镜像开关关闭，跳过启动')
                self._screen_off_proc = start_scrcpy_screen_off()
        except Exception:
            import traceback

            log(f'启动 scrcpy 失败:\n{traceback.format_exc()}')
            self._scrcpy_proc = None
        if self._scrcpy_proc:
            self._embed_tries = 0
            self._embed_fail_logged = False
            self._embed_timer.start(500)


    def _try_embed(self) -> None:
        hwnd = find_scrcpy_hwnd(self._scrcpy_proc)
        if hwnd:
            self.scrcpy_view.embed(hwnd, device_aspect())
            self._fit_screen_card()  # 嵌入后拿到真实画面比例，重算画面卡宽度
            self._embed_timer.stop()
            self._embed_fail_logged = False
            return
        self._embed_tries += 1
        if self._embed_tries >= EMBED_TRIES:
            self._embed_timer.stop()
            # 只报一次：进程还活着时看门狗会周期性补挂嵌入轮询，不重复刷日志
            if not self._embed_fail_logged:
                self._embed_fail_logged = True
                log('未找到 scrcpy 窗口，嵌入失败（调度器仍可正常开始，'
                    '窗口出现后看门狗会自动补嵌入）')

    def _check_scrcpy(self) -> None:
        """看门狗：scrcpy 进程掉了（设备 adb reboot/掉线会断开）就重拉并重嵌入。

        重拉失败（设备还没开机完成）退避 SCRCPY_RETRY_INTERVAL 秒再试，
        避免设备重启期间每 5 秒刷一次失败日志。
        进程活着但没嵌上（多开同时拉起时窗口创建慢、嵌入轮询已超时）也补挂嵌入轮询，
        否则窗口只会孤零零留在屏幕外，画面一直黑着。
        """
        if not self.btn_scrcpy.isChecked():
            return  # 画面镜像已关闭，不自动拉起
        if not window_is_foreground(self) or self.isMinimized():
            if not self._background_mirror_paused:
                self._background_mirror_paused = True
                self._disable_scrcpy()
            return
        if self._background_mirror_paused:
            self._background_mirror_paused = False
            self._enable_scrcpy()
            return
        if not SCRCPY.is_file() or self._embed_timer.isActive():
            return  # 没有 scrcpy 可拉，或启动/重嵌流程正在进行
        if self._scrcpy_proc is not None and self._scrcpy_proc.poll() is None:
            if self.scrcpy_view._hwnd is None:
                self._embed_tries = 0
                self._embed_timer.start(500)
            return  # 活着
        now = time.monotonic()
        if now < self._scrcpy_retry_at:
            return
        had_proc = self._scrcpy_proc is not None
        self.scrcpy_view.set_hwnd(None)
        self._scrcpy_proc = start_scrcpy()
        if self._scrcpy_proc:
            log('scrcpy 已重连' if had_proc else 'scrcpy 已启动')
            self._embed_tries = 0
            self._embed_fail_logged = False
            self._embed_timer.start(500)
        else:
            self._scrcpy_retry_at = now + SCRCPY_RETRY_INTERVAL

    # ---- 当日统计 ----

    def _run_time_prefix(self) -> str:
        """调度器运行时长前缀（未运行时显示 0小时0分钟）。"""
        if (self._runner_started_at is not None
                and self._runner_proc is not None
                and self._runner_proc.poll() is None):
            secs = int(time.monotonic() - self._runner_started_at)
        else:
            secs = 0
        return f'运行时间 {secs // 3600}小时{(secs % 3600) // 60}分钟　'

    def _refresh_queue_card(self) -> None:
        """刷新任务队列卡：当前任务 / 下一任务 / 待执行 / 等待中。

        调度器运行时读 runs/queue_status.json 的精确状态（每秒读一次）；
        未运行时按配置推算（与调度页"下次执行"列共用 _predict_next），
        不开调度器也能看到队列里接下来要跑什么。
        """
        running = self._runner_proc is not None and self._runner_proc.poll() is None
        if running:
            st = load_queue_status()
            if st:
                current = st.get('current') or (
                    f"{st['pending']}（进行中）" if st.get('pending') else '无')
                nxt = st.get('next') or '无'
                if st.get('next_at'):
                    nxt = f"{nxt} {st['next_at']}"
                    # 按时间戳算剩余时间（每次刷新重新算，自然形成倒计时）；
                    # 已过等待点、调度器还没写新一轮状态时会短暂为负，显示"xx前"
                    ts = st.get('next_ts') or 0
                    if ts:
                        delta = ts - time.time()
                        if delta >= 0:
                            nxt += f'（{_format_remaining(delta)}后）'
                        else:
                            nxt += f'（{_format_remaining(-delta)}前）'
                ready, waiting = str(st.get('ready', 0)), str(st.get('waiting', 0))
            else:
                current, nxt = '无', '暂无（等待调度器写入）'
                ready = waiting = '-'
        else:
            current = '无'
            nxt, ready, waiting = self._predict_queue_summary()
        for key, text in (('current', current), ('next', nxt),
                          ('ready', ready), ('waiting', waiting)):
            label = self._queue_values[key]
            label.setText(str(text))
            label.setToolTip(str(text))

    def _predict_queue_summary(self) -> tuple:
        """调度器未运行时按配置推算队列概要：下一任务 / 现在可执行数 / 等待中数。

        按 tasks.order 顺序扫描启用中的任务，第一个非"—"的推算结果就是下一任务
        （order 越靠前越优先，与调度器扫描顺序一致）。
        """
        try:
            cfg = load_config()
            now = datetime.now()
            order = [k.strip() for k in cfg.tasks.order.split('>') if k.strip()]
            ready = waiting = 0
            nxt = '无'
            for key in order:
                if key not in SCHEDULE_TASK_NAMES:
                    continue
                item = getattr(cfg.tasks, key)
                if not item.enabled:
                    continue
                pred = self._predict_next(key, item, cfg, now)
                if pred == '—':
                    continue
                if pred in ('现在可执行', '启动后立即', '启动后判定'):
                    ready += 1
                else:
                    waiting += 1
                if nxt == '无':
                    # "启动后立即"（护理）/"启动后判定"（学习/打工要等金币与时长判定）/
                    # "现在可执行"（支线任务当前在可执行窗口内）这类即时状态提示不展示，
                    # 只保留带具体时间的推算（如"今天 13:10"）
                    nxt = (SCHEDULE_TASK_NAMES[key]
                           if pred in ('启动后立即', '启动后判定', '现在可执行')
                           else f'{SCHEDULE_TASK_NAMES[key]}（{pred}）')
            return nxt, str(ready), str(waiting)
        except Exception:
            return '推算失败', '-', '-'

    def _refresh_stats(self) -> None:
        """刷新主页卡片：运行时间 + 宠物状态横排（状态缓存）+ 任务队列卡 + 各任务当日统计。"""
        if not window_is_foreground(self) or self.isMinimized():
            return  # 后台不反复读配置/统计文件和重绘，回到前台下一秒刷新。
        try:
            self._runtime_label.setText(self._run_time_prefix().strip())
            self._refresh_queue_card()
            accounts = load_accounts()
            if accounts:
                # 单条目（default；老缓存文件可能残留多账号条目，优先取 default，
                # 调度器第一次写状态缓存时会自愈清掉残留条目）
                st = accounts.get('default') or next(iter(accounts.values()))
                for key, _label in STATUS_FIELDS:
                    self._status_values[key].setText(str(st.get(key, '-')))
            else:
                for key, _label in STATUS_FIELDS:
                    self._status_values[key].setText('-')
        except Exception as e:
            self._queue_values['current'].setText(f'状态读取失败: {e}')
        try:
            cfg = load_config()
            tasks = [
                ('学习', SCHOOL_PROGRESS_FILE, cfg.school.times_per_day),
                ('打工', WORK_PROGRESS_FILE, cfg.work.times_per_day),
                ('冒险', ADVENTURE_PROGRESS_FILE, cfg.adventure.times_per_day),
                ('踩踩', VISIT_PROGRESS_FILE, cfg.visit.times_per_day),
                ('PK', PK_PROGRESS_FILE, cfg.pk.times_per_day),
                ('被雇佣', EMPLOYED_PROGRESS_FILE, 0),  # 无次数上限，只显示当日次数
            ]
            study_s, work_s = load_durations(
                cfg.schedule.school_factor, cfg.schedule.work_factor)
            values = {
                # 整数时长不带小数点（0 而不是 0.0），一位小数照常显示
                'study_h': f'{study_s / 3600:.1f}'.removesuffix('.0'),
                'work_h': f'{work_s / 3600:.1f}'.removesuffix('.0'),
            }
            for label, progress_file, limit in tasks:
                _, done, _ = load_progress(progress_file, quiet=True)
                values[label] = f'{done}/{limit}' if limit else str(done)
                if label == '踩踩':
                    # 经验日常（好友照顾）当日是否完成：踩踩次数满但经验未完成时仍会继续
                    _, exp_done, _ = load_exp_daily(quiet=True)
                    values['经验日常'] = '✓' if exp_done else '✗'
            for key, text in values.items():
                self._today_values[key].setText(text)
        except Exception as e:
            self._today_values['study_h'].setText('读取失败')
            self._today_values['study_h'].setToolTip(str(e))
        try:
            self._refresh_schedule()
        except Exception as e:
            log(f'调度状态刷新失败: {e}')

    # ---- 调度页面 ----

    def _build_schedule_page(self) -> QWidget:
        """调度页：每任务 开关 / 执行间隔 / 启用时段 可直接在表格里编辑
        （保存到 config.yaml，调度器下一轮热加载生效），下次执行列每秒刷新
        ——调度器运行时读 queue_status.json 的精确时间，未运行时按配置推算。"""
        page = QWidget()
        page.setObjectName('schedulePage')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 12)
        layout.setSpacing(8)
        self.schedule_table = TableWidget()
        self.schedule_table.setRowCount(0)
        self.schedule_table.setColumnCount(5)
        self.schedule_table.setHorizontalHeaderLabels(
            ['任务', '开关', '执行间隔', '启用时段', '下次执行'])
        self.schedule_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.schedule_table.verticalHeader().setVisible(False)
        self.schedule_table.verticalHeader().setDefaultSectionSize(38)
        self.schedule_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.schedule_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.schedule_table, 1)
        note = CaptionLabel('点击执行间隔/启用时段后修改，回车或离开输入框结束编辑；开关点击切换'
                            '（保存到 config.yaml，调度器下一轮生效）；'
                            '"下次执行"每秒刷新：调度器运行时显示精确时间，未运行时按配置推算；'
                            '学习/打工由主任务组统一调度，无固定间隔，下次执行显示"启动后判定"。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self._schedule_sig: tuple | None = None  # 配置签名：变化且不在编辑中时重建编辑器
        self._schedule_rows: list = []
        self._schedule_order: list = []
        return page

    @staticmethod
    def _clock_to_time(value):
        """把 HH:MM（或 HH:MM:SS / YAML 1.1 解析出的分钟数）转成 datetime.time；
        解析失败返回 None。"""
        if isinstance(value, int):
            value = f'{value // 60:02d}:{value % 60:02d}'
        for fmt in ('%H:%M', '%H:%M:%S'):
            try:
                return datetime.strptime(str(value), fmt).time()
            except ValueError:
                continue
        return None

    @classmethod
    def _parse_range_text(cls, value: str) -> bool:
        """校验启用时段文本：HH:MM-HH:MM 或 HH:MM:SS-HH:MM:SS（允许跨零点）。"""
        try:
            start_s, end_s = str(value).split('-', 1)
        except ValueError:
            return False
        return (cls._clock_to_time(start_s.strip()) is not None
                and cls._clock_to_time(end_s.strip()) is not None)

    @staticmethod
    def _qtime_from_value(value) -> 'QTime':
        """把 HH:MM 字符串 / YAML 1.1 分钟数转成 QTime，非法回退 00:00。"""
        if isinstance(value, int):
            return QTime(value // 60, value % 60)
        t = QTime.fromString(str(value), 'HH:mm')
        return t if t.isValid() else QTime(0, 0)

    # ---- 表格构建 ----

    def _schedule_sig_value(self, cfg, rows: list, order: list) -> tuple:
        """调度表格相关配置的签名：变化且不在编辑中时重建编辑器。"""
        sig = [tuple(order)]
        for key in rows:
            item = getattr(cfg.tasks, key)
            if key in ('adventure', 'visit', 'pk'):
                interval_v = getattr(cfg, key).start_time
            elif key in ('care', 'hire_friend', 'friend_care'):
                interval_v = getattr(cfg, key).interval_seconds
            else:
                interval_v = '—'
            if key in ('hire_friend', 'friend_care'):
                range_v = str(getattr(cfg, key).time_range)
            else:
                range_v = str(item.enabled_time_range)
            sig.append((key, item.enabled, interval_v, range_v))
        return tuple(sig)

    def _schedule_table_editing(self) -> bool:
        """当前焦点是否在调度表格内（编辑中）：是则暂缓重建，避免打断输入。"""
        w = QApplication.focusWidget()
        while w is not None:
            if w is self.schedule_table:
                return True
            w = w.parent()
        return False

    def _rebuild_schedule_table(self, cfg, rows: list, order: list) -> None:
        """重建调度表格：开关/执行间隔/启用时段用可编辑控件，下次执行列占位。"""
        table = self.schedule_table
        table.setRowCount(len(rows))
        for row, key in enumerate(rows):
            item = getattr(cfg.tasks, key)
            in_order = key in order
            name_item = QTableWidgetItem(SCHEDULE_TASK_NAMES[key])
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if not in_order:
                name_item.setToolTip('不在 tasks.order 中，不调度（可在任务选项卡修改顺序）')
            table.setItem(row, 0, name_item)
            table.setCellWidget(row, 1, self._make_switch_editor(key, item, in_order))
            table.setCellWidget(row, 2, self._make_interval_editor(key, item, cfg, in_order))
            table.setCellWidget(row, 3, self._make_range_editor(key, item, cfg, in_order))
            cell = QTableWidgetItem('—')
            cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 4, cell)

    @staticmethod
    def _centered(widget: QWidget) -> QWidget:
        """把控件包进水平居中容器（表格 cellWidget 默认靠左上）。"""
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(widget)
        return wrap

    def _make_switch_editor(self, key: str, item, in_order: bool) -> QWidget:
        cb = SwitchButton()
        cb.setOnText('开')
        cb.setOffText('关')
        cb.setChecked(item.enabled)
        cb.setEnabled(in_order)
        cb.setToolTip('启用/停用该任务（保存到 config.yaml，调度器下一轮生效）'
                      + ('' if in_order else '；不在 tasks.order 中，不调度'))
        cb.checkedChanged.connect(lambda checked, k=key: self._save_schedule_bool(k, checked))
        return self._centered(cb)

    def _make_interval_editor(self, key: str, item, cfg, in_order: bool) -> QWidget:
        if key in ('adventure', 'visit', 'pk'):
            # 每日调度时间（HH:MM）：场景 start_time，保存时同步队列 daily_times
            te = _NoWheelTimeEdit()
            te.setDisplayFormat('HH:mm')
            te.setTime(self._qtime_from_value(getattr(cfg, key).start_time))
            te.setEnabled(in_order)
            te.setToolTip('每日调度时间（HH:MM）')
            te.timeChanged.connect(lambda qt, k=key: self._save_schedule_time(k, qt))
            return ClickToEdit(te)
        if key in ('care', 'hire_friend', 'friend_care'):
            # 调度间隔（秒）：护理用 tasks.care.interval_seconds，好友护理/雇佣好友用场景值
            value = (getattr(cfg.tasks, key).interval_seconds if key == 'care'
                     else getattr(cfg, key).interval_seconds)
            spin = _NoWheelSpinBox()
            spin.setRange(1, 999999)
            spin.setValue(max(1, int(value)))
            spin.setSuffix(' 秒')
            spin.setEnabled(in_order)
            spin.setToolTip('调度间隔（秒）')
            spin.valueChanged.connect(lambda v, k=key: self._save_schedule_interval(k, v))
            return ClickToEdit(spin)
        # 学习/打工：主任务组统一调度，无固定间隔
        label = BodyLabel('—')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setToolTip('学习/打工由主任务组（冒险/学习/打工/雇佣好友）按需统一调度，无固定间隔')
        return label

    def _make_range_editor(self, key: str, item, cfg, in_order: bool) -> QWidget:
        if key in ('hire_friend', 'friend_care'):
            value = str(getattr(cfg, key).time_range)
        else:
            value = str(item.enabled_time_range)
        edit = LineEdit()
        edit.setText(value)
        edit.setEnabled(in_order)
        edit.setPlaceholderText('如 00:00-23:59 或 08:00:00-20:00:00')
        edit.setToolTip('启用时段：HH:MM-HH:MM 或 HH:MM:SS-HH:MM:SS，结束早于开始视为跨零点')
        edit.editingFinished.connect(
            lambda _e=edit, k=key: self._save_schedule_range(k, _e.text()))
        return ClickToEdit(edit)

    # ---- 调度表格保存 ----

    def _save_schedule_values(self, mapping: dict) -> None:
        """把若干配置点写入 config.yaml（一次落盘），值没变不写不刷日志。"""
        if not mapping:
            return
        try:
            data = settings_io.load_raw()
        except Exception as e:
            log(f'读取配置失败: {e}')
            return
        changed = False
        for key, value in mapping.items():
            if settings_io.get_value(data, key) == value:
                continue
            settings_io.set_value(data, key, value)
            changed = True
        if not changed:
            return
        try:
            settings_io.save_raw(data)
        except Exception as e:
            log(f'保存配置失败: {e}')
            return
        first = next(iter(mapping))
        log(f'配置已保存: {first} = {mapping[first]}')
        if self._runner_proc and self._runner_proc.poll() is None:
            log('调度器每轮自动重读配置，最迟下一轮生效（无需重启）')

    def _save_schedule_bool(self, key: str, checked: bool) -> None:
        self._save_schedule_values({f'tasks.{key}.enabled': bool(checked)})

    def _save_schedule_interval(self, key: str, seconds: int) -> None:
        """保存调度间隔：护理/好友护理/雇佣好友同时同步队列与场景两个入口，
        避免队列退避与场景判定不一致。"""
        seconds = max(1, int(seconds))
        if key == 'care':
            mapping = {'tasks.care.interval_seconds': seconds,
                       'care.interval_seconds': seconds}
        elif key in ('hire_friend', 'friend_care'):
            mapping = {f'tasks.{key}.interval_seconds': seconds,
                       f'{key}.interval_seconds': seconds}
        else:
            mapping = {f'tasks.{key}.interval_seconds': seconds}
        self._save_schedule_values(mapping)

    def _save_schedule_time(self, key: str, qtime: 'QTime') -> None:
        """保存每日调度时间：场景 start_time + 队列 daily_times 一起改，保持一致。"""
        value = qtime.toString('HH:mm')
        quoted = DoubleQuotedScalarString(value)
        self._save_schedule_values({f'{key}.start_time': quoted,
                                    f'tasks.{key}.daily_times': [quoted]})

    def _save_schedule_range(self, key: str, text: str) -> None:
        """保存启用时段：好友护理/雇佣好友同时写场景 time_range 与队列
        enabled_time_range；格式非法恢复原值。"""
        value = text.strip()
        if not self._parse_range_text(value):
            log(f'启用时段格式无效: {value!r}，应为 HH:MM-HH:MM 或 HH:MM:SS-HH:MM:SS，已恢复')
            if self._schedule_rows:
                try:
                    self._rebuild_schedule_table(
                        load_config(), self._schedule_rows, self._schedule_order)
                except Exception as e:
                    log(f'恢复调度表格失败: {e}')
            return
        quoted = DoubleQuotedScalarString(value)
        if key in ('hire_friend', 'friend_care'):
            mapping = {f'{key}.time_range': quoted,
                       f'tasks.{key}.enabled_time_range': quoted}
        else:
            mapping = {f'tasks.{key}.enabled_time_range': quoted}
        self._save_schedule_values(mapping)

    # ---- 下次执行 ----

    @staticmethod
    def _parse_dt(value) -> 'datetime | None':
        try:
            return datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
        except (TypeError, ValueError):
            return None

    @classmethod
    def _fmt_next_dt(cls, dt, now) -> str:
        """下次执行时间 -> 详细文本：今天/明天/日期 + HH:MM:SS + 剩余倒计时。"""
        if dt.date() == now.date():
            base = '今天 ' + dt.strftime('%H:%M:%S')
        elif dt.date() == (now + timedelta(days=1)).date():
            base = '明天 ' + dt.strftime('%H:%M:%S')
        else:
            base = dt.strftime('%m-%d %H:%M:%S')
        delta = (dt - now).total_seconds()
        if 0 <= delta < 86400:
            base += f'（{_format_remaining(delta)}后）'
        return base

    @classmethod
    def _in_time_range(cls, now, value: str) -> bool:
        """now 是否在 HH:MM-HH:MM 时间段内（结束早于开始视为跨零点）。"""
        try:
            start_s, end_s = str(value).split('-', 1)
        except ValueError:
            return False
        start = cls._clock_to_time(start_s.strip())
        end = cls._clock_to_time(end_s.strip())
        if start is None or end is None:
            return False
        if end > start:
            return start <= now.time() < end
        return now.time() >= start or now.time() < end

    def _predict_next(self, key: str, item, cfg, now) -> str:
        """调度器未运行/无运行时状态时，按配置推算下次执行（展示用途）。"""
        if key == 'care':
            return '启动后立即'
        if key in ('adventure', 'visit', 'pk'):
            scene = getattr(cfg, key)
            start_t = self._clock_to_time(getattr(scene, 'start_time', '00:00'))
            if start_t is None:
                return '—'
            start_dt = datetime.combine(now.date(), start_t)
            times = int(getattr(scene, 'times_per_day', 0) or 0)
            done = 0
            if times:
                file = {'adventure': ADVENTURE_PROGRESS_FILE, 'visit': VISIT_PROGRESS_FILE,
                        'pk': PK_PROGRESS_FILE}[key]
                _, done, _ = load_progress(file, quiet=True)
            if times and done >= times:
                nxt = start_dt if start_dt > now else start_dt + timedelta(days=1)
                return self._fmt_next_dt(nxt, now)
            if start_dt <= now:
                return '现在可执行'
            return self._fmt_next_dt(start_dt, now)
        if key == 'hire_friend':
            hf = cfg.hire_friend
            if not hf.enabled or not (hf.friend_name or '').strip() or not hf.times_per_day:
                return '—'
            start_t = self._clock_to_time(str(hf.time_range).split('-', 1)[0].strip())
            if start_t is None:
                return '—'
            start_dt = datetime.combine(now.date(), start_t)
            _, done, _ = load_progress(HIRE_FRIEND_PROGRESS_FILE, quiet=True)
            if done >= hf.times_per_day:
                nxt = start_dt if start_dt > now else start_dt + timedelta(days=1)
                return self._fmt_next_dt(nxt, now)
            if self._in_time_range(now, hf.time_range):
                return '现在可执行'
            nxt = start_dt if start_dt > now else start_dt + timedelta(days=1)
            return self._fmt_next_dt(nxt, now)
        if key == 'friend_care':
            fc = cfg.friend_care
            if not fc.enabled or not (fc.friend_name or '').strip():
                return '—'
            start_t = self._clock_to_time(str(fc.time_range).split('-', 1)[0].strip())
            if start_t is None:
                return '—'
            start_dt = datetime.combine(now.date(), start_t)
            if self._in_time_range(now, fc.time_range):
                return '现在可执行'
            nxt = start_dt if start_dt > now else start_dt + timedelta(days=1)
            return self._fmt_next_dt(nxt, now)
        if key in ('school', 'work'):
            return '启动后判定'
        return '—'

    def _next_exec_text(self, key: str, item, cfg, state: dict, running: bool,
                        now, in_order: bool) -> str:
        """下次执行列文本：调度器运行时优先用 queue_status.json 的精确时间，
        否则按配置推算。"""
        if not in_order or not item.enabled:
            return '—'
        if running and state:
            st = state.get('state')
            if st == 'disabled':
                return '—'
            if st == 'ready':
                return '现在可执行'
            if st == 'dead':
                nxt = self._parse_dt(state.get('next'))
                return '当天已结束' if nxt is None else self._fmt_next_dt(nxt, now)
            if st == 'waiting':
                nxt = self._parse_dt(state.get('next'))
                return '—' if nxt is None else self._fmt_next_dt(nxt, now)
            return '—'
        return self._predict_next(key, item, cfg, now)

    def _refresh_schedule(self) -> None:
        """刷新调度选项卡：配置变化（且不在编辑中）重建编辑器；下次执行列每秒更新。
        调度器运行时读 runs/queue_status.json 的精确时间，未运行按配置推算。"""
        cfg = load_config()
        running = self._runner_proc is not None and self._runner_proc.poll() is None
        states = (load_queue_status() or {}).get('tasks', {}) if running else {}
        order = [k.strip() for k in cfg.tasks.order.split('>') if k.strip()]
        # 表格顺序 = tasks.order，不在 order 里的任务排最后并标注不调度
        rows = [k for k in order if k in SCHEDULE_TASK_NAMES]
        rows += [k for k in TASK_KEYS if k not in rows]
        self._schedule_rows = rows
        self._schedule_order = order
        sig = self._schedule_sig_value(cfg, rows, order)
        if sig != self._schedule_sig:
            if not self._schedule_table_editing():
                self._rebuild_schedule_table(cfg, rows, order)
                self._schedule_sig = sig
        now = datetime.now()
        for row, key in enumerate(rows):
            item = getattr(cfg.tasks, key)
            in_order = key in order
            text = self._next_exec_text(key, item, cfg, states.get(key, {}),
                                        running, now, in_order)
            cell = QTableWidgetItem(text)
            cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if text == '现在可执行':
                cell.setForeground(QColor('#7cfc90'))
            if text != '—':
                if running and states.get(key):
                    tip = '调度器运行中（精确时间）'
                elif running:
                    tip = '调度器运行中但未写入该任务状态，按配置推算'
                else:
                    tip = '调度器未运行，按当前配置推算'
            else:
                tip = ''
            cell.setToolTip(tip)
            self.schedule_table.setItem(row, 4, cell)

    # ---- 设置/任务页面 ----

    def _make_field_widget(self, key: str, kind) -> QWidget:
        """按字段类型创建表单控件并注册到 self._setting_widgets（保存信号在此连接）。

        返回放入表单行的控件（'text' 类型返回含教程链接的列容器）。
        """
        if kind == 'int':
            w = _NoWheelSpinBox()
            # 体力/清洁是 0-100，其余次数/阈值放宽
            w.setRange(0, 100 if key.startswith('care.') else 99999)
            # 用 valueChanged 而非 editingFinished：滚轮/滚动导致的失焦不再误触发保存，
            # 只有数值真的变化（箭头/键盘/输入提交）才保存；_load_settings 用 blockSignals 防误存
            w.valueChanged.connect(lambda _v, k=key: self.save_field(k))
        elif kind == 'bool':
            w = SwitchButton()
            w.setOnText('开')
            w.setOffText('关')
            w.checkedChanged.connect(lambda _c, k=key: self.save_field(k))
        elif kind == 'text':
            # 多行文本（如 OnePush YAML 配置）：失焦自动保存，下方附配置教程链接
            w = _FocusOutPlainTextEdit(lambda k=key: self.save_field(k))
            w.setMinimumHeight(60)
            w.setMaximumHeight(110)
            w.setPlaceholderText('provider: bark\nkey: 你的Key')
            # HyperlinkLabel 的 (url, text) 重载要求 url 是 QUrl——传 str 会被当成
            # （text, parent) 重载，显示原始 URL 且点击无效
            help_label = HyperlinkLabel(QUrl(ONEPUSH_HELP_URL), 'OnePush 配置教程')
            col = QWidget()
            col_layout = QVBoxLayout(col)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(4)
            col_layout.addWidget(w)
            col_layout.addWidget(help_label)
            self._setting_widgets[key] = (w, kind)
            return col
        elif kind == 'devices' or isinstance(kind, list):
            if kind == 'devices':
                # 设备序列号：可编辑下拉——既可从在线设备里选，也可手动输入
                w = _NoInsertEditableComboBox()
                w.setPlaceholderText('自动（第一台）或输入序列号，如 127.0.0.1:7555')
                w.setToolTip('从下拉选择在线设备，或直接输入设备序列号/无线调试地址')
                # 手动输入每敲一个字符就保存会反复重启 scrcpy/调度器，
                # 只在 选了下拉项（activated）或 输入结束（Enter/失焦）时保存
                w.activated.connect(lambda _i, k=key: self.save_field(k))
                w.editingFinished.connect(lambda k=key: self.save_field(k))
            else:
                w = ComboBox()
                w.addItems(kind)
                w.currentTextChanged.connect(lambda _t, k=key: self.save_field(k))
                if key == 'care.method':
                    # 护理方式变化时联动显隐体力/清洁阈值（一键护理不读状态，阈值无意义）
                    w.currentTextChanged.connect(self._on_care_method_changed)
        else:
            w = LineEdit()
            # 时间类字段：格式提示不占标签宽度，放在 placeholder 里
            if key.endswith('.start_time'):
                w.setPlaceholderText('HH:MM')
            elif key.endswith('.time_range'):
                w.setPlaceholderText('HH:MM-HH:MM（结束早于开始视为跨零点）')
            w.editingFinished.connect(lambda k=key: self.save_field(k))
        self._setting_widgets[key] = (w, kind)
        return w

    def _build_settings_form(self, fields: list) -> tuple:
        """按字段列表构建设置表单页（表单编辑 config.yaml，字段失焦自动保存，保留注释）。

        字段按配置键第一段分组（SETTING_GROUP_TITLES），每组一张 HeaderCardWidget，
        组顺序按字段首次出现；卡片在 TwoColumnCardsPanel 里按高度平衡进左右两列
        （字段填完后 finalize，用 sizeHint 估算高度塞到较矮的一列）。
        返回 (容器控件, {组标题: QFormLayout}, {组标题: 卡片})。

        设置页和任务页共用：控件都注册进 self._setting_widgets，
        加载/保存逻辑（load_settings / save_field）不区分来自哪个页。
        """
        container = TwoColumnCardsPanel()
        group_forms: dict = {}
        group_cards: dict = {}
        for key, label, kind in fields:
            title = SETTING_GROUP_TITLES.get(key.split('.')[0], '其他')
            form = group_forms.get(title)
            if form is None:
                card = CompactCardWidget()
                card.setTitle(title)
                form = QFormLayout()
                form.setContentsMargins(0, 0, 0, 0)
                form.setSpacing(10)
                card.viewLayout.addLayout(form)
                group_forms[title] = form
                group_cards[title] = card
                container.add_card(card)
            w = self._make_field_widget(key, kind)
            form.addRow(BodyLabel(label), w)
            field_w = self._setting_widgets[key][0]
            if key in ('care.energy_threshold', 'care.clean_threshold'):
                self._care_threshold_rows[key] = (form.labelForField(w), field_w)
        container.finalize()  # 字段已填完，按卡片实际高度平衡进两列
        return container, group_forms, group_cards

    def _wrap_form_page(self, container: QWidget) -> QWidget:
        """把表单容器包进 Fluent 滚动区域页（透明背景，透出窗口主题底色）。"""
        scroll = ScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        container.setStyleSheet('background: transparent;')
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 12)
        layout.addWidget(scroll)
        return page

    def _build_tasks_page(self) -> QWidget:
        """任务页：任务队列执行顺序 + 各场景任务相关设置。"""
        page = self._wrap_form_page(self._build_settings_form(TASK_SETTING_FIELDS)[0])
        page.setObjectName('tasksPage')
        return page

    def _build_settings_page(self) -> QWidget:
        """设置页：连接/调度引擎/全局规则/告警等全局设置 + 关于与更新。"""
        container, group_forms, group_cards = self._build_settings_form(SETTING_FIELDS)
        # 通知测试：按当前 config.yaml 的 notify 配置发一条测试告警。
        # 点击按钮会先让输入框失焦（失焦自动保存），未落盘的修改也会先生效；
        # 各渠道发送结果见日志页
        test_btn = PushButton('发送通知测试', self, FIF.SEND)
        test_btn.clicked.connect(self._test_notify)
        test_row = QWidget()
        test_row_layout = QHBoxLayout(test_row)
        test_row_layout.setContentsMargins(0, 0, 0, 0)
        test_row_layout.addWidget(test_btn)
        test_row_layout.addStretch(1)
        group_forms['告警通知'].addRow(BodyLabel('通知测试'), test_row)
        # 检查更新：启动后自动查一次，之后每 6 小时一次；这里手动触发，
        # 结果显示在旁边的标签上（有更新时是可点击的 Release 链接）
        about_card = CompactCardWidget()
        about_card.setTitle('关于与更新')
        about_form = QFormLayout()
        about_form.setContentsMargins(0, 0, 0, 0)
        about_form.setSpacing(10)
        about_card.viewLayout.addLayout(about_form)
        self._update_label = BodyLabel(f'当前版本 v{APP_VERSION}')
        self._update_label.setOpenExternalLinks(True)
        self._update_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        update_btn = PushButton('检查更新', self, FIF.UPDATE)
        update_btn.clicked.connect(lambda: self._start_update_check(manual=True))
        update_row = QWidget()
        update_layout = QHBoxLayout(update_row)
        update_layout.setContentsMargins(0, 0, 0, 0)
        update_layout.setSpacing(8)
        update_layout.addWidget(update_btn)
        update_layout.addWidget(self._update_label, 1)
        about_form.addRow(BodyLabel('检查更新'), update_row)
        close_actions = ['关闭程序', '最小化程序']
        close_combo = ComboBox()
        close_combo.addItems(close_actions)
        close_combo.setToolTip('选择最小化时，点击右上角 × 后托管继续运行；Alt+F4 仍退出程序。')
        self._setting_widgets['gui.close_action'] = (close_combo, close_actions)
        close_combo.currentTextChanged.connect(lambda _: self.save_field('gui.close_action'))
        about_form.addRow(BodyLabel('右上角关闭按钮'), close_combo)
        container.add_card(about_card)
        page = self._wrap_form_page(container)
        page.setObjectName('settingsPage')
        return page


    def _on_tab_changed(self, index: int) -> None:
        """切到任务页或设置页时加载当前配置（按页面 objectName 判定，不依赖页序）。"""
        w = self.stackedWidget.widget(index)
        if w is not None and w.objectName() in ('tasksPage', 'settingsPage'):
            self.load_settings()

    def _on_care_method_changed(self, method: str) -> None:
        """护理方式选"一键护理"时隐藏体力/清洁阈值行（不读状态，阈值用不上）。"""
        hidden = method == '一键护理'
        for label, w in self._care_threshold_rows.values():
            label.setVisible(not hidden)
            w.setVisible(not hidden)

    def _test_notify(self) -> None:
        """设置页"通知测试"按钮：发一条测试告警，各渠道结果打到日志页。"""
        from src.notify import send_alert  # 按需导入（winotify/onepush 均为懒加载）

        log('发送通知测试...')
        sent = send_alert('通知测试：收到这条说明告警渠道配置正常')
        log('通知测试已送达' if sent else '通知测试未送达（检查配置，各渠道详情见上方日志）')

    # ---- 检查更新 ----

    def _start_update_check(self, manual: bool) -> None:
        """后台线程查 GitHub 最新 Release；manual=True 时结果弹窗提示。"""
        if self._update_checking:
            if manual:
                box = MessageBox('检查更新', '正在检查更新，请稍候。', self)
                box.cancelButton.hide()
                box.yesButton.setText('好')
                box.exec()
            return
        self._update_checking = True
        threading.Thread(target=self._update_check_worker, args=(manual,),
                         daemon=True).start()

    def _update_check_worker(self, manual: bool) -> None:
        from src.update_checker import check_github_latest_release

        result = check_github_latest_release(APP_GITHUB_REPO, APP_VERSION)
        self._sig_update_result.emit((manual, result))

    def _on_update_result(self, payload) -> None:
        manual, result = payload
        self._update_checking = False
        if result.ok and result.has_update:
            self._update_label.setText(
                f'发现新版本 <a href="{result.release_url}">{result.latest_tag}</a>'
                f'（当前 v{result.current_version}）')
            log(f'发现新版本 {result.latest_tag}（当前 v{result.current_version}），'
                f'下载：{result.release_url}')
        elif result.ok:
            self._update_label.setText(f'当前已是最新版本（v{result.current_version}）')
        else:
            self._update_label.setText(result.message)
            if not manual:
                log(result.message)
        if manual:
            self._show_update_result_dialog(result)

    def _show_update_result_dialog(self, result) -> None:
        """手动检查更新的结果弹窗 打开发布页/稍后 两个按钮，
        点"打开发布页"直接用浏览器打开 Release 页面。"""
        ok = result.ok
        if ok and result.has_update:
            latest = result.latest_tag or f'v{result.latest_version}'
            title = '发现新版本'
            text = f'当前版本: v{result.current_version}\n最新版本: {latest}'
            yes_text, later_text = '打开发布页', '稍后'
        elif ok:
            title = '检查完成'
            text = f'当前版本: v{result.current_version}\n当前已是最新版本。'
            yes_text, later_text = '好', '关闭'
        else:
            title = '检查失败'
            text = f'{result.message}\n\n可手动查看发布地址:\n{result.release_url}'
            yes_text, later_text = '打开发布页', '关闭'
        box = MessageBox(title, text, self)
        box.yesButton.setText(yes_text)
        box.cancelButton.setText(later_text)
        if box.exec() and yes_text == '打开发布页':
            QDesktopServices.openUrl(QUrl(result.release_url or APP_RELEASES_URL))

    def _get_test_dev(self) -> 'U2Device':
        """测试用 u2 连接：懒加载并复用；adb 路径/序列号变化时自动重建。

        供 OCR/控件树测试的后台线程调用（须已持有 self._test_lock）。
        """
        from src.config import find_adb

        cfg = load_config()
        key = (find_adb(cfg.adb.path), cfg.adb.device_serial)
        if self._test_dev is None or self._test_dev_key != key:
            from src.u2dev import U2Device

            self._test_dev = U2Device(key[0], key[1])
            self._test_dev_key = key
        return self._test_dev

    def _get_adb_dev(self) -> Device:
        """GUI 侧 adb Device（维持屏幕关闭用），懒加载并按 adb 配置变化重建。"""
        from src.config import find_adb

        cfg = load_config()
        key = (find_adb(cfg.adb.path), cfg.adb.device_serial)
        if self._adb_dev is None or self._adb_dev_key != key:
            self._adb_dev = Device(key[0], key[1])
            self._adb_dev_key = key
        return self._adb_dev

    def _set_test_btn_enabled(self, enabled: bool) -> None:
        """启用/禁用"连接测试"按钮（避免测试期间重复触发）。"""
        try:
            self._btn_connect_test.setEnabled(enabled)
        except RuntimeError:  # 窗口已关闭，控件已销毁
            pass

    def _test_connect(self) -> None:
        """顶部"连接测试"按钮：u2 截图 + OCR 识别 + 控件树拉取 的实测耗时。

        不计入 u2 连接与 OCR 引擎首次加载/预热时间（预热一轮后计时），
        后台线程执行，结果打到日志页。
        """
        self._set_test_btn_enabled(False)

        def work() -> None:
            try:
                from src.ocr import get_engine, ocr_fullscreen

                with self._test_lock:
                    dev = self._get_test_dev()
                    w, h = dev.window_size()
                    log(f'[连接测试] 设备 {w}x{h}，u2 连接就绪（不计时）')
                    get_engine()  # OCR 引擎首次加载（懒加载，不计时）
                    log('[连接测试] OCR 引擎已加载（首次加载不计时）')
                    # 预热一轮：截图+OCR、控件树各一次（首次调用初始化，不计时）
                    ocr_fullscreen(dev.screenshot())
                    dev.d.dump_hierarchy()
                    log('[连接测试] 预热完成（不计时），开始计时...')
                    # OCR 部分：截图 + 识别
                    for i in (1, 2):
                        t0 = time.perf_counter()
                        screen = dev.screenshot()
                        t1 = time.perf_counter()
                        results = ocr_fullscreen(screen)
                        t2 = time.perf_counter()
                        shot_ms = (t1 - t0) * 1000
                        ocr_ms = (t2 - t1) * 1000
                        log(f'[连接测试] OCR 第{i}轮: 截图 {shot_ms:.1f}ms + 识别 {ocr_ms:.1f}ms'
                            f' = {shot_ms + ocr_ms:.1f}ms，识别 {len(results)} 处文本')
                    # 控件树部分：整树拉取
                    for i in (1, 2):
                        t0 = time.perf_counter()
                        xml = dev.d.dump_hierarchy()
                        t1 = time.perf_counter()
                        ms = (t1 - t0) * 1000
                        size_kb = len(xml.encode('utf-8')) / 1024
                        nodes = xml.count('<node')
                        log(f'[连接测试] 控件树 第{i}轮: 拉取 {ms:.1f}ms，'
                            f'节点 {nodes} 个，XML {size_kb:.1f}KB')
            except Exception as e:
                log(f'[连接测试] 失败: {e}')
            finally:
                try:  # 窗口可能已关闭（信号对象已销毁）
                    self._test_signals.finished.emit(True)
                except RuntimeError:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _manual_recover(self) -> None:
        """顶部"手动重启"按钮：按 recover.method 配置执行一次异常恢复
        （重启设备/重启游戏 -> 回宠物主页），后台线程执行。

        调度器在跑会先停掉：恢复要重启设备/强停 QQ，调度器的 u2 连接必然失效，
        让它自己撞异常恢复不如先停干净；恢复完成自动启动调度器
        （_on_recover_finished），与"开始/停止"按钮联动——恢复期间两个按钮都
        禁用（避免中途误点"开始"撞上正在重启的设备），恢复完成调度器跑起来后
        "停止"自然可用。
        """
        self._btn_manual_recover.setEnabled(False)
        self._recovering = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        if self._runner_proc and self._runner_proc.poll() is None:
            log('手动重启：先停止调度器')
            self.stop_runner()

        def work() -> None:
            ok = False
            try:
                from src.recover import reenter_pet

                cfg = load_config()
                log(f'手动重启：按配置执行异常恢复（recover.method={cfg.recover.method}）...')
                reenter_pet(
                    self._get_adb_dev(),
                    method=cfg.recover.method)
                ok = True
                log('手动重启完成，已回宠物主页')
            except Exception as e:
                log(f'手动重启失败: {e}')
            finally:
                try:  # 窗口可能已关闭（信号对象已销毁）
                    self._recover_signals.finished.emit(ok)
                except RuntimeError:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _on_recover_finished(self, recovered: bool) -> None:
        """手动重启结束（主线程）：恢复按钮可用；恢复完成后自动启动调度器。

        恢复完成后启动调度，启动检查会确认宠物主页。
        """
        try:
            self._btn_manual_recover.setEnabled(True)
        except RuntimeError:  # 窗口已关闭，控件已销毁
            return
        self._recovering = False
        log('手动重启结束，启动调度器')
        self.start_runner()

    def load_settings(self) -> None:
        try:
            data = settings_io.load_raw()
        except Exception as e:
            log(f'读取配置失败: {e}')
            return
        for key, (w, kind) in self._setting_widgets.items():
            value = settings_io.get_value(data, key)
            if value is None:
                # 旧 config.yaml 可能缺新增字段：回退默认值，避免 int('') 崩溃
                value = settings_io.DEFAULTS.get(key, '')
            w.blockSignals(True)  # 加载时不触发自动保存
            if kind == 'devices':
                self._fill_devices(w, str(value))
            elif kind == 'int':
                w.setValue(int(value))
            elif kind == 'bool':
                # 配置里缺该键时回退 DEFAULTS，避免与运行时的 dataclass 默认值不一致
                w.setChecked(bool(settings_io.DEFAULTS.get(key) if value == '' else value))
            elif kind == 'text':
                w.setPlainText(str(value))
            elif isinstance(kind, list):
                idx = w.findText(str(value))
                if idx >= 0:
                    w.setCurrentIndex(idx)
                else:
                    # 旧配置可能是列表外的值（如下拉收窄后的旧地点）：回退默认
                    w.setCurrentText(str(settings_io.DEFAULTS.get(key, '')))
            else:
                w.setText(str(value))
            w.blockSignals(False)
        # blockSignals 抑制了 care.method 的联动信号，加载后手动同步阈值行显隐
        method_w, _ = self._setting_widgets.get('care.method', (None, None))
        if method_w is not None:
            self._on_care_method_changed(method_w.currentText())

    def _fill_devices(self, combo: '_NoInsertEditableComboBox', current: str) -> None:
        """枚举在线手机并显示型号，允许手动填写无线调试地址。"""
        combo.clear()
        # 注意 fluent ComboBox.addItem 签名是 (text, icon=None, userData=None)：
        # userData 必须关键字传，位置传参会被当成 icon，data 全是 None（选啥都存成空）
        combo.addItem('自动（第一台）', userData='')
        try:
            from src.adb.device import Device
            from src.config import find_adb

            dev = Device(find_adb(load_config().adb.path))
            for serial in dev.online_devices():
                label = None
                if label is None:
                    phone = Device(dev.adb, serial)
                    brand, model = (phone.getprop('ro.product.brand'),
                                    phone.getprop('ro.product.model'))
                    if model:
                        label = model if brand.lower() in model.lower() \
                            else f'{brand} {model}'.strip()
                combo.addItem(f'{serial}（{label}）' if label else serial, userData=serial)
        except Exception as e:
            log(f'枚举设备失败: {e}')
        idx = combo.findData(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setText(current)  # 下拉里没有的自定义序列号，填回编辑框

    def save_field(self, key: str) -> None:
        """字段失焦自动保存：校验 -> 值没变直接返回 -> 写回 config.yaml ->
        adb 连接字段重拉 scrcpy/防抖重启调度器，其余字段调度器每轮热加载生效。"""
        w, kind = self._setting_widgets[key]
        if kind == 'devices':
            # 可编辑下拉：用户手动输入不匹配任何下拉项时，Qt 仍保留上次选中项的
            # currentData（会误判成已选中的值），所以统一按显示文本反查：
            # 与某选项显示文本一致 → 取该项 data（选项文本带实例名标注，不能直接存）；
            # 手动输入（不匹配任何选项）直接用文本本身；"自动（第一台）" → ''
            text = w.currentText().strip()
            idx = w.findText(text)
            if idx >= 0:
                value = w.itemData(idx) or ''
            else:
                value = '' if text == '自动（第一台）' else text
        elif kind == 'int':
            value = w.value()
        elif kind == 'bool':
            value = w.isChecked()
        elif kind == 'text':
            value = w.toPlainText().strip()
        elif isinstance(kind, list):
            value = w.currentText()
        else:
            value = w.text().strip()
        ok, fixed = settings_io.validate_field(key, value)
        if not ok:
            log(f'配置 {key} 的值 {value!r} 无效，已恢复默认值 {fixed!r}')
            w.blockSignals(True)  # 恢复默认值不再触发一次保存
            if kind == 'devices':
                idx = w.findData(fixed)
                if idx >= 0:
                    w.setCurrentIndex(idx)
                else:
                    w.setText(str(fixed))
            elif kind == 'int':
                w.setValue(fixed)
            elif kind == 'bool':
                w.setChecked(bool(fixed))
            elif kind == 'text':
                w.setPlainText(str(fixed))
            elif isinstance(kind, list):
                w.setCurrentText(fixed)
            else:
                w.setText(str(fixed))
            w.blockSignals(False)
        try:
            data = settings_io.load_raw()  # 重新读取，避免覆盖其他字段
            current = settings_io.get_value(data, key)
            # 值没变（失焦/信号误触发，如切换选项卡导致的 editingFinished）：
            # 不写文件、不打日志、不触发 scrcpy 重拉/调度器重启
            if current is not None and current == fixed:
                return
            settings_io.set_value(data, key, fixed)
            settings_io.save_raw(data)
        except Exception as e:
            log(f'保存配置失败: {e}')
            return
        log(f'配置已保存: {key} = {fixed}')
        if key == 'gui.theme':
            # 主题即时切换（qfluentwidgets 支持运行时 setTheme），无需重启
            setTheme(THEME_MAP.get(str(fixed), Theme.AUTO))
        if key in ('adb.device_serial', 'adb.path'):
            # adb 连接相关：重拉 scrcpy，调度器也需要重启重建连接
            self._restart_scrcpy()
            if self._runner_proc and self._runner_proc.poll() is None:
                self._restart_timer.start()  # 防抖：连续修改多个字段只重启一次
        elif self._runner_proc and self._runner_proc.poll() is None:
            log('调度器每轮自动重读配置，最迟下一轮生效（无需重启）')


    def _restart_scrcpy(self) -> None:
        """杀掉并重拉 scrcpy（换设备/换 adb 后画面也需要切换）。"""
        if not self.btn_scrcpy.isChecked():
            return  # 开关关闭时不启动 scrcpy
        log('重新初始化 scrcpy...')
        kill_our_scrcpy(self._scrcpy_proc)
        self.scrcpy_view.set_hwnd(None)
        self._scrcpy_proc = start_scrcpy()
        if self._scrcpy_proc:
            self._embed_tries = 0
            self._embed_fail_logged = False
            self._embed_timer.start(500)

    def _toggle_scrcpy(self, _checked: bool = False) -> None:
        """scrcpy 开关切换：开=启动并嵌入，关=结束进程且不再自动拉起。
        开关状态持久化到 gui.mirror（下次启动保持）。"""
        try:
            data = settings_io.load_raw()
            if settings_io.get_value(data, 'gui.mirror') != self.btn_scrcpy.isChecked():
                settings_io.set_value(data, 'gui.mirror', self.btn_scrcpy.isChecked())
                settings_io.save_raw(data)
        except Exception as e:
            log(f'保存画面镜像开关状态失败: {e}')
        if self.btn_scrcpy.isChecked():
            log('开启 scrcpy...')
            self._enable_scrcpy()
        else:
            self._disable_scrcpy()

    def _enable_scrcpy(self) -> None:
        """启动 scrcpy 并开始查找嵌入（看门狗随后自动维护重连）。"""
        if self._background_mirror_paused:
            return
        if self._screen_off_proc is not None and self._screen_off_proc.poll() is None:
            log('结束屏幕关闭 scrcpy')
            self._screen_off_proc.terminate()
            try:
                self._screen_off_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._screen_off_proc.kill()
                self._screen_off_proc.wait(timeout=2)
        self._screen_off_proc = None
        if self._scrcpy_proc is not None and self._scrcpy_proc.poll() is None:
            # 已在运行：若之前嵌入超时没嵌上（窗口落在屏幕外），补挂嵌入轮询而不是干等
            if self.scrcpy_view._hwnd is None and not self._embed_timer.isActive():
                self._embed_tries = 0
                self._embed_timer.start(500)
            return
        self.scrcpy_view.set_hwnd(None)
        self._scrcpy_proc = start_scrcpy()
        if self._scrcpy_proc:
            self._embed_tries = 0
            self._embed_fail_logged = False
            self._embed_timer.start(500)

    def _disable_scrcpy(self) -> None:
        """结束 scrcpy 并停止嵌入/看门狗维护（开关关闭状态）。"""
        self._embed_timer.stop()
        kill_our_scrcpy(self._scrcpy_proc)
        self._scrcpy_proc = None
        self.scrcpy_view.set_hwnd(None)
        # 镜像关闭：用无头 scrcpy 真正关掉设备屏幕（保持自动化可用）；
        if self._screen_off_proc is None or self._screen_off_proc.poll() is not None:
            self._screen_off_proc = start_scrcpy_screen_off()

    def _restart_runner(self) -> None:
        if self._runner_proc and self._runner_proc.poll() is None:
            log('重启调度器使配置即时生效...')
            self.stop_runner()
            QTimer.singleShot(500, self.start_runner)

    # ---- 调度器控制：开始 = 启动子进程，停止 = 结束子进程 ----

    def start_runner(self) -> None:
        """以较低 CPU 优先级启动真机调度器子进程。"""
        if self._runner_proc and self._runner_proc.poll() is None:
            return
        log('启动调度器...')
        env = dict(os.environ, PYTHONIOENCODING='utf-8', QQPET_PROFILE_ID=PROFILE_ID)
        # onefile 子进程默认复用父进程的 _MEI 解压目录（_MEIPASS2 环境变量），
        # 调度器快速杀拉/并发时可能把共享目录搞坏（表现为惰性导入的模块/资源
        # 突然"找不到"）；去掉后 runner 用自己的独立解压目录，互不影响
        env.pop('_MEIPASS2', None)
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, '--runner']  # 打包后：以 --runner 参数重启自身
        else:
            cmd = [sys.executable, '-u', str(RUNNER_SCRIPT)]
        self._runner_proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            creationflags=_NO_WINDOW | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0),
        )
        threading.Thread(
            target=self._read_runner_logs, args=(self._runner_proc,), daemon=True
        ).start()
        self._runner_started_at = time.monotonic()

    def stop_runner(self) -> None:
        if self._runner_proc and self._runner_proc.poll() is None:
            log('结束调度器进程')
            if sys.platform == 'win32' and getattr(sys, 'frozen', False):
                # onefile 包装进程还有实际 runner 子进程，切配置前一起停止。
                subprocess.run(['taskkill', '/PID', str(self._runner_proc.pid), '/T', '/F'],
                               creationflags=_NO_WINDOW, capture_output=True, timeout=10)
            else:
                self._runner_proc.terminate()
            try:
                self._runner_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._runner_proc.kill()
                self._runner_proc.wait(timeout=5)
        self._runner_started_at = None

    def _read_runner_logs(self, proc: subprocess.Popen) -> None:
        """把调度器子进程的输出逐行送入日志队列。"""
        for line in proc.stdout:
            self._log_queue.put(line.rstrip())
        log('调度器已结束')

    # ---- 日志刷新 ----

    def _drain_logs(self) -> None:
        bar = self.log_view.verticalScrollBar()
        # 只有用户本来就在底部时才跟随新日志；向上翻看时保持当前位置
        was_at_bottom = bar.value() >= bar.maximum() - 2
        added = False
        while True:
            try:
                line = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_view.appendPlainText(line)
            added = True
        if added and was_at_bottom:
            bar.setValue(bar.maximum())
        # 按调度器进程状态同步按钮；手动重启进行中保持两个按钮禁用
        # （_manual_recover 里已禁用，_drain_logs 每 100ms 刷新时不能按进程状态放开）
        running = bool(self._runner_proc and self._runner_proc.poll() is None)
        if not running:
            self._runner_started_at = None
        if self._recovering:
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
        else:
            self.btn_start.setEnabled(not running)
            self.btn_stop.setEnabled(running)

    # ---- 退出 ----

    def _on_title_close(self):
        # 只重定向标题栏 ×，不拦截配置切换、Alt+F4 或正常退出的资源清理。
        if load_config().gui.close_action == '最小化程序':
            self.showMinimized()
        else:
            self.close()

    def closeEvent(self, event) -> None:
        self._restart_timer.stop()
        self._scrcpy_watchdog.stop()
        self._embed_timer.stop()
        self.stop_runner()
        # 只结束由本程序拉起的 scrcpy
        if self._scrcpy_proc and self._scrcpy_proc.poll() is None:
            log('关闭 scrcpy')
            kill_our_scrcpy(self._scrcpy_proc)
        if self._screen_off_proc and self._screen_off_proc.poll() is None:
            log('结束屏幕关闭 scrcpy')
            kill_our_scrcpy(self._screen_off_proc)
        event.accept()


def _ensure_runtime_resources() -> None:
    """确保 scrcpy 已就位（缺失不阻塞，后台线程）。

    源码运行：缺失时自动调用对应 fetch 工具下载（幂等），失败给出手动下载地址与放置位置。
    打包运行（frozen）：资源随包或放在 exe 旁 runs/（可写数据目录，覆盖随包资源）；缺失时提示
    可手动放到 exe 旁 runs/ 的对应目录（或重新打包），不联网下载。
    """
    frozen = getattr(sys, 'frozen', False)

    def work() -> None:
        if not SCRCPY.is_file():
            if frozen:
                log(f'未找到 scrcpy。需要画面镜像请手动放置（或重新打包），exe 旁 runs 目录：'
                    f'{APP_ROOT / "runs" / "resources" / "scrcpy-win64"}/（内含 scrcpy.exe）')
            else:
                log('未找到 scrcpy，正在自动下载（tools/fetch_scrcpy.py）...')
                fetch = APP_ROOT / 'tools' / 'fetch_scrcpy.py'
                if fetch.is_file():
                    subprocess.run([sys.executable, str(fetch)], check=False)
                if SCRCPY.is_file():
                    log('scrcpy 已就绪')
                else:
                    # 下载失败不阻塞：给出下载地址与放置位置，用户手动处理
                    log(f'scrcpy 自动下载失败。请手动下载 scrcpy win64 并解压到 {SCRCPY.parent}：\n'
                        f'  地址: https://github.com/Genymobile/scrcpy/releases （scrcpy-win64-vX.zip，需含 scrcpy.exe）')
    threading.Thread(target=work, daemon=True).start()






def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='QQ 宠物真机助手')
    parser.add_argument('--runner', action='store_true', help='启动后台调度器')
    args = parser.parse_args()
    if args.runner:
        # 调度器子进程模式（打包后由 GUI 以 --runner 参数拉起）
        # windowed 打包的程序 stdout 用本地编码(GBK)，强制改 UTF-8，否则 GUI 日志乱码
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, 'reconfigure'):
                try:
                    stream.reconfigure(encoding='utf-8', errors='replace')
                except (OSError, ValueError):
                    pass  # windowed 下 stdout/stderr 可能是无效流
        from scenarios.runner import run_scheduler

        # 与控制台入口一致：按 config.yaml 的 runner.engine 选调度引擎
        # （之前这里写死 legacy Runner，导致打包版不写 runs/queue_status.json）
        run_scheduler()
        return
    _ensure_runtime_resources()
    app = QApplication(sys.argv)
    # 主题：跟随系统/深色/浅色（gui.theme 配置，默认跟随系统）
    setTheme(THEME_MAP.get(load_config().gui.theme, Theme.AUTO))
    window = MainWindow()
    window.show()
    code = app.exec()
    next_profile = getattr(window, '_next_profile', None)
    if next_profile:
        env = dict(os.environ, QQPET_PROFILE_ID=next_profile,
                   PYINSTALLER_RESET_ENVIRONMENT='1')
        env.pop('_MEIPASS2', None)
        cmd = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(APP_ROOT / 'main.py')]
        subprocess.Popen(cmd, cwd=str(APP_ROOT), env=env, creationflags=_NO_WINDOW)
    sys.exit(code)


if __name__ == '__main__':
    main()
