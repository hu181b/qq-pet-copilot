"""config.yaml 读写（ruamel 往返模式，保留注释和格式），供设置页面使用。"""
from __future__ import annotations

from datetime import datetime
from io import StringIO

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString

from .config import CONFIG_FILE, MAIN_TASK_KEYS, TASK_KEYS
from .atomic_file import atomic_write_text

_yaml = YAML()  # 默认 round-trip，保留注释

# 打工地点可选列表（设置页下拉 + 校验共用，新增地图时在这里加）
WORK_LOCATIONS = ('风铃旅社', '彩虹画室', '迷雾侦探所', '星尘魔法塔',
                  '咕噜厨房', '竹影武馆', '云朵梦舍', '闪耀星屋')

# 各配置项默认值：设置页校验不通过时恢复
DEFAULTS = {
    'adb.path': 'resources/scrcpy-win64/adb.exe',
    'adb.device_serial': '',
    'gui.theme': '跟随系统',
    'gui.close_action': '关闭程序',
    'gui.mirror': True,
    'control.method': 'injectInputEvent',
    'school.attribute': '力量',
    'school.times_per_day': 0,
    'work.location': '风铃旅社',
    'work.times_per_day': 0,
    'work.employ_scroll_limit': 5,
    'schedule.coin_threshold': 2000,
    'schedule.daily_hour_limit': 8,
    'schedule.check_interval': 8,
    'schedule.main_page_checks': 1,
    'schedule.back_method': '系统返回',
    'schedule.encourage_times': 10,
    'adventure.times_per_day': 1,
    'adventure.start_time': '08:00',
    'adventure.skip_bad_weather': True,
    'adventure.batch': 12,
    'visit.times_per_day': 10,
    'visit.start_time': '00:01',
    'pk.times_per_day': 15,
    'pk.start_time': '00:01',
    'friend_care.enabled': False,
    'friend_care.time_range': '14:00-19:30',
    'friend_care.friend_name': '',
    'friend_care.method': 'ocr检测',
    'friend_care.interval_seconds': 60,
    'hire_friend.enabled': False,
    'hire_friend.time_range': '19:31-23:59',
    'hire_friend.interval_seconds': 5,
    'hire_friend.friend_name': '',
    'hire_friend.times_per_day': 8,
    'runner.engine': 'task_queue',
    'tasks.order': 'svip>care>school>friend_care>hire_friend>adventure>visit>pk>work',
    'tasks.main_order': 'school>hire_friend>adventure>work',
    'tasks.failure_interval': 1800,
    'care.energy_threshold': 60,
    'care.clean_threshold': 60,
    'care.method': 'ocr检测',
    'care.interval_seconds': 60,
    'work.duration': '10分钟',
    'employed.enabled': False,
    'employed.time_range': '19:31-23:59',
    'employed.interval_seconds': 60,
    'employed.action': '等到25/75（小于45min）',
    'notify.win_toast': True,
    'notify.onepush_config': '',
}


def validate_field(key: str, value):
    """校验单个配置项，返回 (是否通过, 修正后的值)；不通过时给出默认值。"""
    default = DEFAULTS.get(key)
    if key in ('adventure.start_time', 'visit.start_time', 'pk.start_time'):
        try:
            datetime.strptime(str(value), '%H:%M')
            # 必须带引号写回：9:00 不带引号会被 YAML 1.1 解析成整数 540
            return True, DoubleQuotedScalarString(str(value))
        except ValueError:
            return False, DoubleQuotedScalarString(str(default))
    if key == 'work.location':
        return (True, value) if value in WORK_LOCATIONS else (False, default)
    if key == 'school.attribute':
        return (True, value) if value in ('力量', '智力', '魅力') else (False, default)
    if key == 'work.duration':
        return (True, value) if value in ('10分钟', '45分钟', '2小时') else (False, default)
    if key == 'care.method' or key == 'friend_care.method':
        return (True, value) if value in ('ocr检测', '一键护理') else (False, default)
    if key in ('friend_care.time_range', 'employed.time_range', 'hire_friend.time_range'):
        try:
            start_s, end_s = str(value).split('-', 1)
            datetime.strptime(start_s.strip(), '%H:%M')
            datetime.strptime(end_s.strip(), '%H:%M')
            # 必须带引号写回：9:00 不带引号会被 YAML 1.1 解析成整数 540
            return True, DoubleQuotedScalarString(str(value))
        except ValueError:
            return False, DoubleQuotedScalarString(str(default))
    if key == 'friend_care.friend_name' or key == 'hire_friend.friend_name':
        return True, str(value).strip()
    if key in ('friend_care.enabled', 'hire_friend.enabled', 'employed.enabled'):
        return (True, value) if isinstance(value, bool) else (False, default)
    if key == 'runner.engine':
        return (True, value) if value in ('task_queue', 'legacy') else (False, default)
    if key == 'control.method':
        return (True, value) if value in ('injectInputEvent', 'minitouch') else (False, default)
    if key == 'gui.theme':
        return (True, value) if value in ('跟随系统', '深色', '浅色') else (False, default)
    if key == 'gui.close_action':
        return (True, value) if value in ('关闭程序', '最小化程序') else (False, default)
    if key == 'tasks.order':
        keys = [k.strip() for k in str(value).split('>') if k.strip()]
        if keys and all(k in TASK_KEYS for k in keys):
            return True, '>'.join(keys)
        return False, default
    if key == 'tasks.main_order':
        # 主任务组内优先级：只需要求非空且都是主任务名；没列出的按默认顺序兜底
        keys = [k.strip() for k in str(value).split('>') if k.strip()]
        if keys and all(k in MAIN_TASK_KEYS for k in keys):
            return True, '>'.join(keys)
        return False, default
    if key in ('care.energy_threshold', 'care.clean_threshold'):
        try:
            return (True, value) if 0 <= int(value) <= 100 else (False, default)
        except (TypeError, ValueError, OverflowError):
            return False, default
    if key == 'schedule.back_method':
        return (True, value) if value in ('返回图标', '系统返回') else (False, default)
    if key == 'tasks.failure_interval':
        # 失败重试间隔至少 1 秒（0 会变成无间隔连续重试死循环）
        try:
            return (True, value) if int(value) >= 1 else (False, default)
        except (TypeError, ValueError):
            return False, default
    if key in ('schedule.check_interval', 'schedule.main_page_checks'):
        # 检查间隔至少 1 秒 / 检测次数至少 1 次（0 会变成无间隔死循环或不设防点 back）
        try:
            return (True, value) if int(value) >= 1 else (False, default)
        except (TypeError, ValueError):
            return False, default
    if key in ('employed.interval_seconds', 'hire_friend.interval_seconds',
               'care.interval_seconds'):
        # 调度间隔至少 1 秒（0 会变成无间隔连续调度）
        try:
            return (True, value) if int(value) >= 1 else (False, default)
        except (TypeError, ValueError):
            return False, default
    if key == 'notify.win_toast' or key == 'adventure.skip_bad_weather' \
            or key == 'gui.mirror':
        return (True, value) if isinstance(value, bool) else (False, default)
    if key == 'notify.onepush_config':
        # OnePush 推送配置（YAML，支持多行）：留空，或能解析出含 provider 的字典
        text = str(value).strip()
        if not text:
            return True, ''
        try:
            cfg = yaml.safe_load(text)
        except yaml.YAMLError:
            return False, default
        if not (isinstance(cfg, dict) and cfg.get('provider')):
            return False, default
        # 多行用块样式写回（保留换行可读性），单行仍用带引号标量
        if '\n' in text:
            return True, LiteralScalarString(text)
        return True, DoubleQuotedScalarString(text)
    # 其余整数字段（次数/阈值/系数）非负即可，空字符串不允许
    if isinstance(DEFAULTS.get(key), int):
        try:
            return (True, value) if int(value) >= 0 else (False, default)
        except (TypeError, ValueError):
            return False, default
    return True, value


def load_raw():
    """读取 config.yaml 为可修改的映射对象（保留注释）。"""
    with open(CONFIG_FILE, encoding='utf-8') as f:
        return _yaml.load(f) or {}


def save_raw(data) -> None:
    """写回 config.yaml（保留原有注释和格式）。"""
    stream = StringIO()
    _yaml.dump(data, stream)
    atomic_write_text(CONFIG_FILE, stream.getvalue())


def get_value(data, dotted_key: str, default=None):
    """按 'school.attribute' 形式的点路径取值。"""
    cur = data
    for part in dotted_key.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_value(data, dotted_key: str, value) -> None:
    """按点路径写值，中间层级不存在则创建。"""
    parts = dotted_key.split('.')
    cur = data
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


# 版本升级时新增的任务键：老配置的 tasks.order 里没有，启动时自动追加到队尾。
# 只在进程启动时迁移一次（GUI 主进程 + 调度器子进程各一次）；
# 用户此后手动从 order 删掉的键不会再被加回来。以后新增任务时扩充这个元组。
NEW_TASK_KEYS = ('svip',)


def migrate_tasks_order() -> None:
    """老配置的 tasks.order 缺少新任务键时，插入到队首并写回（保留注释）。

    新任务（如 svip）是每天只跑一次的轻量任务，放最前=最高优先：
    到点后调度器下一轮就先执行它，不用等前面长任务排队。
    """
    from src.progress import log

    try:
        data = load_raw()
        order = str(get_value(data, 'tasks.order') or '').strip()
        if not order:
            return  # 空 order 由调度器按全部任务键兜底，不需要迁移
        keys = [k.strip() for k in order.split('>') if k.strip()]
        missing = [k for k in NEW_TASK_KEYS if k not in keys]
        if not missing:
            return
        set_value(data, 'tasks.order', '>'.join(missing + keys))
        save_raw(data)
        log(f'tasks.order 缺少新任务 {"/".join(missing)}，已插入到队首'
            f'（重排顺序请到任务选项卡修改）')
    except Exception as e:  # noqa: BLE001 - 迁移失败不阻断启动
        log(f'tasks.order 迁移失败（{e}），请手动在任务选项卡把新任务加进执行顺序')
