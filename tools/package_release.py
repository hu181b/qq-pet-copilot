"""用白名单构建便携发行压缩包，不收集运行目录或个人配置。"""
import argparse
import hashlib
import zipfile
from pathlib import Path


def package(exe: Path, output: Path, tag: str):
    root = Path(__file__).resolve().parents[1]
    if not exe.is_file():
        raise FileNotFoundError(exe)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'QQPetCopilot-{tag}-windows-x64.zip'
    files = [(exe, 'QQPetCopilot.exe'), (root/'LICENSE', 'LICENSE'),
             (root/f'RELEASE-{tag}.md', '使用说明与版本优化.md')]
    for path, _ in files:
        if not path.is_file():
            raise FileNotFoundError(path)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path, name in files:
            z.write(path, name)
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist()) == {name for _, name in files}
        assert z.testzip() is None
    checksums = output/'SHA256SUMS.txt'
    checksums.write_text('\n'.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}'
        for path in (archive, exe))+'\n', encoding='utf-8')
    print(f'{archive} ({archive.stat().st_size} bytes)')
    print(checksums)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--exe', type=Path, default=Path('dist/QQPetCopilot.exe'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--tag', default='v1.3')
    args = p.parse_args()
    package(args.exe, args.output, args.tag)
