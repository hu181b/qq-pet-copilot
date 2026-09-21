"""每日次数/时长进度持久化（对外兼容入口，底层统一由 src/progress_store.py 管理）。

进度文件固定为 runs/ 下的单文件（学习/打工/冒险/踩踩/PK/被雇佣/雇佣好友/
经验日常各一个）。跨天规整、原子写入、损坏兜底都在 src/progress_store.py，
这里只保留文件常量、日志与各场景/runner/GUI 依赖的函数签名，保证调用方不变。
测试重定向进度文件仍按老习惯 monkeypatch 本模块的文件常量即可（函数在调用时
才读模块全局常量，会拿到新值）。
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from .config import PROJECT_ROOT

SCHOOL_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'school_progress.json'
WORK_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'work_progress.json'
ADVENTURE_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'adventure_progress.json'
EMPLOYED_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'employed_progress.json'
VISIT_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'visit_progress.json'
PK_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'pk_progress.json'
HIRE_FRIEND_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'hire_friend_progress.json'
EXP_DAILY_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'exp_daily_progress.json'
SVIP_PROGRESS_FILE = PROJECT_ROOT / 'runs' / 'svip_progress.json'


def log(msg: str) -> None:
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    try:  # 打包成 --windowed 后没有控制台，stdout 可能是无效流，不能让它拖垮日志
        print(line, flush=True)
    except (OSError, UnicodeError):
        pass
    try:  # 写入日志文件 runs/logs/YYYY-MM-DD.log
        log_dir = PROJECT_ROOT / 'runs' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f'{date.today().isoformat()}.log', 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass
    for listener in _log_listeners:
        try:
            listener(line)
        except Exception:
            pass


_log_listeners: list = []


def add_log_listener(fn) -> None:
    """注册日志监听器（GUI 实时日志用），每行日志都会回调 fn(line)。"""
    _log_listeners.append(fn)


from . import progress_store  # noqa: E402  (需要先定义 log 再注册给 store)

progress_store.set_logger(log)


def load_progress(progress_file: Path, quiet: bool = False) -> tuple[str, int, dict]:
    """读取持久化进度，返回 (今天日期, 当天已完成次数, 历史记录 {日期: 次数})。

    跨天时把旧日期的次数归档进 history（内存归档，不落盘）。quiet=True 时不打印续跑日志。
    """
    today, done, history = progress_store.load_daily(progress_file)
    if done and not quiet:
        log(f'读取到今天已完成 {done} 次，继续计数')
    return today, done, history


def save_progress(progress_file: Path, today: str, done: int, history: dict) -> None:
    """持久化当天次数和历史记录 {日期: 次数}。

    底层 save_daily 会先做跨天规整：旧日期的累计时长（study_secs/work_secs）等
    会被清掉，根治"昨天的工作时长漏进今天"；写入走原子替换。
    """
    progress_store.save_daily(progress_file, today, done, history)


def log_history(history: dict, today: str) -> None:
    """启动时打印历史记录（不含今天）。"""
    past = {d: c for d, c in sorted(history.items()) if d != today and c}
    if past:
        log('历史记录: ' + '，'.join(f'{d} {c} 次' for d, c in past.items()))


def increment_progress(progress_file: Path) -> int:
    """给指定进度文件的当天次数 +1 并保存，返回新次数。"""
    return progress_store.increment_daily(progress_file)


# ---- 经验日常（踩踩时顺带做：好友页照顾区域有 exp 就点一键护理） ----


def load_exp_daily(quiet: bool = False) -> tuple[str, bool, dict]:
    """读取经验日常进度，返回 (今天日期, 当日是否完成, 历史记录 {日期: 是否完成})。"""
    today, done, history = progress_store.load_exp_daily(EXP_DAILY_PROGRESS_FILE)
    if not quiet:
        log(f'经验日常: ' + ('已完成' if done else '未完成'))
    return today, done, history


def save_exp_daily(done: bool, today: str | None = None, history: dict | None = None) -> None:
    """持久化当天经验日常是否完成和历史记录。"""
    progress_store.save_exp_daily(EXP_DAILY_PROGRESS_FILE, done, today, history)


def exp_daily_done() -> bool:
    """今天经验日常是否已完成（供调度判断是否仍需处理）。"""
    _, done, _ = load_exp_daily(quiet=True)
    return done


def log_exp_daily() -> None:
    """打印经验日常当天状态与历史（日志/GUI 显示用）。"""
    today, done, history = load_exp_daily(quiet=True)
    past = {d: v for d, v in sorted(history.items()) if d != today}
    line = f'经验日常: ' + ('已完成' if done else '未完成')
    if past:
        line += '（历史: ' + '，'.join(f'{d} ' + ('完成' if v else '未完成')
                                      for d, v in past.items()) + '）'
    log(line)


# ---- 每日领取 QQ SVIP 会员礼包（布尔位：当天是否已领取） ----


def load_svip_claim(quiet: bool = False) -> tuple[str, bool, dict]:
    """读取 SVIP 礼包进度，返回 (今天日期, 当日是否已领取, 历史记录 {日期: 是否领取})。"""
    today, done, history = progress_store.load_exp_daily(SVIP_PROGRESS_FILE)
    if not quiet:
        log('SVIP礼包: ' + ('今天已领取' if done else '今天未领取'))
    return today, done, history


def save_svip_claim(done: bool, today: str | None = None, history: dict | None = None) -> None:
    """持久化当天 SVIP 礼包是否已领取（跨天由 progress_store 规整，隔天自动失效）。"""
    progress_store.save_exp_daily(SVIP_PROGRESS_FILE, done, today, history)


def svip_claimed_today() -> bool:
    """今天 SVIP 礼包是否已领取（供调度判断当天是否还需要执行）。"""
    _, done, _ = load_svip_claim(quiet=True)
    return done


def set_svip_nonmember(flag: bool) -> None:
    """记录"任务因非会员被自动关闭"标记（供调度器每日复查会员是否恢复）。"""
    data = progress_store.read_raw(SVIP_PROGRESS_FILE)
    data['non_member'] = bool(flag)
    data['marked_at'] = time.strftime('%Y-%m-%d %H:%M:%S') if flag else ''
    progress_store.write_raw(SVIP_PROGRESS_FILE, data)


def svip_nonmember_flag() -> bool:
    """当前是否处于"非会员自动关闭"状态（关闭期间调度器每日探测一次）。"""
    return bool(progress_store.read_raw(SVIP_PROGRESS_FILE).get('non_member'))


def reset_daily_progress_for_pet(pet_name: str) -> list[str]:
    """换宠物/账号（OCR 识别的宠物名变化）后清空所有每日任务进度。

    保留历史记录，仅当天计数清零；经验日常/SVIP 领取状态一并重置。
    返回有计数被清掉的任务名（供日志说明）。
    """
    today = date.today().isoformat()
    reset: list[str] = []
    for name, file in (('学习', SCHOOL_PROGRESS_FILE),
                       ('打工', WORK_PROGRESS_FILE),
                       ('冒险', ADVENTURE_PROGRESS_FILE),
                       ('被雇佣', EMPLOYED_PROGRESS_FILE),
                       ('踩踩', VISIT_PROGRESS_FILE),
                       ('PK', PK_PROGRESS_FILE),
                       ('雇佣好友', HIRE_FRIEND_PROGRESS_FILE)):
        _, done, history = progress_store.load_daily(file)
        if done:
            progress_store.save_daily(file, today, 0, history)
            reset.append(name)
    _, exp_done, exp_history = load_exp_daily(quiet=True)
    if exp_done:
        save_exp_daily(False, today, exp_history)
        reset.append('经验日常')
    _, svip_done, svip_history = load_svip_claim(quiet=True)
    if svip_done:
        save_svip_claim(False, today, svip_history)
        reset.append('SVIP礼包')
    if reset:
        log(f'宠物名变为 {pet_name}，已重置每日任务进度: {"/".join(reset)}')
    return reset


# ---- 成长福袋累计金币（跨天累计；换账号/配置变更后清零重计） ----

MONEYBAG_STATS_FILE = PROJECT_ROOT / 'runs' / 'moneybag_stats.json'


def _moneybag_config_sig() -> str:
    """config.yaml 指纹（修改时间 + 大小）：配置文件一变，福袋累计清零重计。"""
    try:
        st = (PROJECT_ROOT / 'config.yaml').stat()
        return f'{st.st_mtime_ns}-{st.st_size}'
    except OSError:
        return ''


def add_moneybag_coins(coins: int, bags: int = 1, pet_name: str | None = None) -> dict:
    """累加一次福袋领取的金币/个数并落盘，返回更新后的累计。

    清零条件（满足其一）：config.yaml 变更；宠物名与上次记录不同（换账号）。
    宠物名从未识别出来时不影响累计（不当作换账号）。
    """
    data = progress_store.read_raw(MONEYBAG_STATS_FILE)
    sig = _moneybag_config_sig()
    name = str(pet_name or '').strip()
    prev_name = str(data.get('pet_name') or '').strip()
    if data.get('config_sig') != sig or (name and prev_name and name != prev_name):
        data = {}  # 换账号或配置变更：清零重计
    new = {
        'pet_name': name or prev_name,
        'config_sig': sig,
        'coins': progress_store.to_int(data.get('coins', 0)) + int(coins or 0),
        'bags': progress_store.to_int(data.get('bags', 0)) + max(1, int(bags or 1)),
        'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    progress_store.write_raw(MONEYBAG_STATS_FILE, new)
    if new['coins']:
        log(f'福袋累计: 本次 +{coins} 金币，共 {new["coins"]} 金币'
            f'（{new["bags"]} 个，宠物 {new["pet_name"] or "未识别"}）')
    return new


def load_moneybag_stats() -> dict:
    """读取福袋累计（供 GUI 今日统计显示）。"""
    data = progress_store.read_raw(MONEYBAG_STATS_FILE)
    return {
        'pet_name': str(data.get('pet_name') or ''),
        'coins': progress_store.to_int(data.get('coins', 0)),
        'bags': progress_store.to_int(data.get('bags', 0)),
    }


# 活动类型 -> (进度文件, 中文量词, 计数名)，用于出门时等完别的活动后的交叉计数
CROSS_PROGRESS = {
    'school': (SCHOOL_PROGRESS_FILE, '一节课', '学习'),
    'work': (WORK_PROGRESS_FILE, '一次打工', '打工'),
    'adventure': (ADVENTURE_PROGRESS_FILE, '一次冒险', '冒险'),
    'employed': (EMPLOYED_PROGRESS_FILE, '一次被雇佣', '被雇佣'),
}


def count_cross(finished: str) -> None:
    """出门时等完了别的活动，计入对应进度并打日志；学习/打工顺带累计时长。"""
    file, unit, name = CROSS_PROGRESS[finished]
    n = increment_progress(file)
    log(f'出门时等完了{unit}，已计入{name}次数（{n} 次）')
    if finished == 'school':
        record_study_finish()
    elif finished == 'work':
        record_work_finish()


# ---- 学习/工作时长累计（替代旧"每日点数"规则，按学园/打工时长结算） ----
# 各学园一节课对应的学习时长（秒）
SCHOOL_DURATION_SECONDS = {
    '初级学园': 10 * 60,
    '中级学园': 20 * 60,
    '高级学园': 30 * 60,
    '进修学院': 45 * 60,
}
# 打工时长配置 -> 单次打工时长（秒）
WORK_DURATION_SECONDS = {
    '10分钟': 10 * 60,
    '45分钟': 45 * 60,
    '2小时': 2 * 3600,
}


def get_current_school() -> str | None:
    """school_progress.json 里持久化的当前学园（学习开始时写入，跨天视为空）。"""
    return progress_store.get_daily_field(SCHOOL_PROGRESS_FILE, 'school')


def set_current_school(school: str) -> None:
    """学习开始时记录当前学园：跨天时总是落盘推进日期，否则值变化才写（减少落盘）。"""
    progress_store.set_daily_field(SCHOOL_PROGRESS_FILE, 'school', school)


def get_current_work_duration() -> str | None:
    """work_progress.json 里持久化的本次打工时长（work.duration）。"""
    return progress_store.get_daily_field(WORK_PROGRESS_FILE, 'duration')


def set_current_work_duration(duration: str) -> None:
    """打工开始时记录本次打工时长（work.duration）：跨天总是落盘，否则值变化才写。"""
    progress_store.set_daily_field(WORK_PROGRESS_FILE, 'duration', duration)


def _today_seconds(progress_file: Path, key: str) -> int:
    return progress_store.today_seconds(progress_file, key)


def _add_seconds(progress_file: Path, key: str, seconds: int) -> int:
    return progress_store.add_seconds(progress_file, key, seconds)


def record_study_finish() -> int | None:
    """一节课结算：按持久化的学园累计学习时长（秒），返回当天累计或 None（学园未知）。"""
    school = get_current_school()
    secs = SCHOOL_DURATION_SECONDS.get(school or '')
    if not secs:
        return None
    total = _add_seconds(SCHOOL_PROGRESS_FILE, 'study_secs', secs)
    log(f'学习结算: {school} +{secs // 60} 分钟，今日已学习 {total // 60} 分钟')
    return total


def record_work_finish() -> int | None:
    """一次打工结算：按持久化的打工时长累计打工时长（秒）。"""
    duration = get_current_work_duration()
    secs = WORK_DURATION_SECONDS.get(duration or '')
    if not secs:
        return None
    total = _add_seconds(WORK_PROGRESS_FILE, 'work_secs', secs)
    log(f'打工结算: 时长 {duration} +{secs // 60} 分钟，今日已打工 {total // 60} 分钟')
    return total


def _migrate_old_durations(progress_file: Path, key: str, minutes_per: int) -> None:
    """老版本进度今天只有次数（learned）、没有时长字段时，按
    次数 x minutes_per 分钟 换算时长并落盘（只迁移一次，之后 study_secs 存在即跳过）。"""
    if minutes_per <= 0:
        return
    data = progress_store.read_raw(progress_file)
    if data.get('date') != date.today().isoformat():
        return
    if key in data:
        return  # 已有新字段（含 0），跳过
    count = progress_store.to_int(data.get('learned', 0))
    if count <= 0:
        return
    data[key] = count * minutes_per * 60
    progress_store.write_raw(progress_file, data)
    log(f'迁移老进度: {progress_file.stem} 今天 {count} 次 x {minutes_per} 分钟'
        f' = {count * minutes_per} 分钟')


def load_durations(school_factor: int = 0, work_factor: int = 0) -> tuple[int, int]:
    """今天已累计 (学习秒, 打工秒)。

    首次运行新版本：老进度今天只有次数没有时长时，按旧版 学习/打工点数系数
    （即每节/每次的分钟数）自动换算补上，之后正常累计。
    """
    _migrate_old_durations(SCHOOL_PROGRESS_FILE, 'study_secs', school_factor)
    _migrate_old_durations(WORK_PROGRESS_FILE, 'work_secs', work_factor)
    return (_today_seconds(SCHOOL_PROGRESS_FILE, 'study_secs'),
            _today_seconds(WORK_PROGRESS_FILE, 'work_secs'))
