"""Local GUI exception/native fault records; never part of release payload data."""
from __future__ import annotations

import faulthandler
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

_crash_stream = None


def install_gui_diagnostics(directory: Path, logger) -> None:
    """Keep the fault stream alive and record exceptions escaping Qt callbacks."""
    global _crash_stream
    if _crash_stream is not None:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'gui-errors-{os.getpid()}.log'
        stream = path.open('a', encoding='utf-8', buffering=1)
        stream.write(f'GUI started {datetime.now().isoformat()} pid={os.getpid()}\n')
        faulthandler.enable(file=stream, all_threads=True)
        _crash_stream = stream
    except OSError as exc:
        logger(f'无法建立界面异常记录：{exc}')
        return

    def exception_hook(exc_type, value, tb):
        # Qt 槽未捕获异常默认会触发 qFatal；保存 traceback 并中止本次操作。
        try:
            stream.write(f'\n{datetime.now().isoformat()} GUI callback exception\n')
            traceback.print_exception(exc_type, value, tb, file=stream)
            stream.flush()
            logger(f'界面操作异常：{exc_type.__name__}；详情已记录到 {path.name}')
        except Exception:
            # 错误记录本身也不能再次从 Qt 回调抛出。
            pass

    sys.excepthook = exception_hook
