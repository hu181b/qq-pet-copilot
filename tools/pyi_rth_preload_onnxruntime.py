"""Preload onnxruntime before PyQt6 runtime hook.

参考 qq-farm-copilot：PyInstaller 内置的 pyi_rth_pyqt6 hook 会先 import
PyQt6.QtCore，某些 Windows 环境下可能导致之后 onnxruntime 初始化失败。
这里先加载 ORT，锁定兼容的 DLL 初始化顺序（打包时经 --runtime-hook 注入）。
"""

import sys
import os
from pathlib import Path

# 自动化单张推理不需要 BLAS/OpenMP 大线程池，必须在导入 NumPy/ORT 前设置。
for _key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[_key] = '1'

# 与 scrcpy 共享同一份 ADB，避免再打包 adbutils 自带的副本。
if getattr(sys, 'frozen', False):
    _adb = Path(sys._MEIPASS) / 'resources' / 'scrcpy-win64' / 'adb.exe'
    if _adb.is_file():
        os.environ.setdefault('ADBUTILS_ADB_PATH', str(_adb))


def _preload_onnxruntime() -> None:
    if sys.platform != 'win32':
        return
    import onnxruntime  # noqa: F401


_preload_onnxruntime()
del _preload_onnxruntime
