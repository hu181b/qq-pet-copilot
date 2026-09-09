"""构建真机版：python build.py [--onedir]。"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

def main() -> None:
    parser=argparse.ArgumentParser(description='构建 QQ 宠物真机版')
    parser.add_argument('--onedir',action='store_true',help='输出目录模式，默认单文件')
    args=parser.parse_args()
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8',errors='replace')
    for script in ('fetch_ocr_models.py','fetch_scrcpy.py'):
        subprocess.run([sys.executable,str(PROJECT_ROOT/'tools'/script)],check=False)
    subprocess.run([sys.executable,str(PROJECT_ROOT/'tools/fetch_minitouch.py'),
                    '--arch','arm64-v8a'],check=False)
    env=dict(os.environ)
    env.pop('QQ_PET_EMULATOR',None)
    if args.onedir:
        env['QQ_PET_ONEDIR']='1'
    else:
        env.pop('QQ_PET_ONEDIR',None)
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean',
                    'QQPetCopilot.spec'],cwd=PROJECT_ROOT,env=env,check=True)

if __name__=='__main__':
    main()
