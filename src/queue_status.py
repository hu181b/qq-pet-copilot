"""任务队列状态缓存：调度器（task_queue 引擎）每轮写一次，GUI 日志页状态条显示用。

runs/queue_status.json:
{"current": "当前正在执行的任务名", "pending": "主任务组进行中活动（延时收尾）",
 "next": "下一任务名", "next_at": "HH:MM:SS", "next_ts": 下一任务时间戳（算剩余秒数用）,
 "ready": 待执行数量（在等退避/每日窗口/pending 收尾时间）,
 "waiting": 等待中数量（现在就可执行、等调度器轮到）, "updated": "HH:MM:SS",
 "tasks": {"<任务键>": {"state": "disabled/dead/ready/waiting",
           "next": "YYYY-MM-DD HH:MM:SS 下次执行时间（可能为空）"}}}

只是展示用途，读写失败都不影响调度/界面。
"""
from __future__ import annotations

import json
import time

from .config import PROJECT_ROOT
from .atomic_file import atomic_write_text

QUEUE_STATUS_FILE = PROJECT_ROOT / 'runs' / 'queue_status.json'
_last_write = None


def save_queue_status(state: dict) -> None:
    """调度器每轮调度后写一次队列状态（写失败只影响展示，不抛异常）。"""
    global _last_write
    try:
        # updated 是心跳时间，其他状态没变时最多每 30 秒写一次。
        key = json.dumps({k:v for k,v in state.items() if k != 'updated'},
                         ensure_ascii=False, sort_keys=True)
        now = time.monotonic()
        path = QUEUE_STATUS_FILE.resolve()
        if (_last_write is not None and _last_write[:2] == (path,key)
                and now - _last_write[2] < 30 and path.is_file()):
            return
        atomic_write_text(path, json.dumps(state, ensure_ascii=False))
        _last_write = (path,key,now)
    except OSError:
        pass


def load_queue_status() -> dict | None:
    """GUI 读队列状态（文件不存在/损坏返回 None）。"""
    try:
        data = json.loads(QUEUE_STATUS_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
