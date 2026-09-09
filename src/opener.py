"""真机 QQ 官方页面跳转，不含注入与设备伪装。"""
import subprocess
import sys
import time
from .progress import log
PET_SCHEME = 'mqqapi://qpet/open?version=1&src_type=app&source=1'
QQ_SETTLE_WAIT=3.0
_NO_WINDOW=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
class OpenPetPageError(RuntimeError):
    pass

def _adb_run(adb: str, serial: str | None, *args: str,
             check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = [adb]
    if serial:
        cmd += ['-s', serial]
    cmd += list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout, creationflags=_NO_WINDOW)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise OpenPetPageError(detail or f'adb 命令失败: {" ".join(cmd)}')
    return proc

def _wait_qq_settle(adb: str, serial: str) -> None:
    """等 QQ 启动稳定再打开宠物页。

    焦点离开 SplashActivity 即认为就绪（热启动很快）；冷启动最久等
    QQ_SETTLE_WAIT 秒。过早跳转/注入会被 QQ 启动流程干扰。
    """
    deadline = time.monotonic() + QQ_SETTLE_WAIT
    while time.monotonic() < deadline:
        focus = _adb_run(adb, serial, 'shell', 'dumpsys window | grep -E "mCurrentFocus"',
                         check=False, timeout=30).stdout or ''
        if 'SplashActivity' not in focus:
            return
        time.sleep(1)
    log(f'等待 {QQ_SETTLE_WAIT:.0f}s 后 QQ 仍在启动页，继续打开流程')

def _open_pet_via_scheme(adb: str, serial: str) -> None:
    """官方 scheme 跳转打开宠物主页（JumpActivity，普通 shell 即可，无需 root）。"""
    _adb_run(adb, serial, 'shell', 'am', 'start', '-a', 'android.intent.action.VIEW',
             '-d', PET_SCHEME, check=False, timeout=30)
