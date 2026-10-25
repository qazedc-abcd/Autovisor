"""从白名单文件组装便携版，绝不复制开发者配置、日志或登录记录。"""
import hashlib
import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

from modules.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != 'nt' or not ((3, 10) <= sys.version_info[:2] <= (3, 13)):
        raise SystemExit('请使用 Windows 的 Python 3.10–3.13 构建。')
    from modules.installer import runtime_packages
    for package, expected in runtime_packages().items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            raise SystemExit('构建依赖不完整，请先执行 uv sync --extra gui --extra captcha。')
        if actual != expected:
            raise SystemExit(f'构建需要 {package}=={expected}，当前为 {actual}；请同步依赖。')
    stage = (ROOT / 'build' / 'package-stage').resolve()
    bundle = (ROOT / 'dist' / 'Autovisor').resolve()
    for path, parent in ((stage, ROOT / 'build'), (bundle, ROOT / 'dist')):
        assert path.is_relative_to(parent.resolve()) and path != parent.resolve()
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
    # 避免开发环境 PATH 中其他软件的同名 DLL 污染依赖收集。
    # Qt 使用 Windows 系统 ICU；Poppler/Conda 的 icuuc.dll 导出符号不兼容。
    environment = os.environ.copy()
    windows = Path(os.environ['SystemRoot'])
    environment['PATH'] = os.pathsep.join(map(str, [
        Path(sys.prefix) / 'Scripts', Path(sys.base_prefix), Path(sys.base_prefix) / 'DLLs',
        windows / 'System32', windows,
    ]))
    for name, entry, contents, windowed in (
        ('Autovisor', 'GUI.py', 'internal', True),
        ('AutovisorWorker', 'Autovisor.py', 'engine', False),
    ):
        command = [
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir',
            '--contents-directory', contents, '--name', name,
            '--distpath', str(stage / 'output'), '--workpath', str(stage / name),
            '--specpath', str(stage), '--icon', str(ROOT / 'resources' / 'zhs.ico'),
        ]
        if windowed:
            command += ['--windowed', '--exclude-module', 'playwright', '--exclude-module', 'numpy', '--exclude-module', 'cv2']
        else:
            command += [
                '--console', '--exclude-module', 'PySide6', '--hidden-import', 'cv2',
                '--hidden-import', 'numpy', '--copy-metadata', 'numpy',
                '--copy-metadata', 'opencv-python',
            ]
        subprocess.run(command + [str(ROOT / entry)], cwd=ROOT, env=environment, check=True)
        shutil.copytree(stage / 'output' / name, bundle, dirs_exist_ok=True)
    (bundle / 'resources').mkdir()
    (bundle / 'data').mkdir()
    for name in ('stealth.min.js', 'zhs.ico'):
        shutil.copy2(ROOT / 'resources' / name, bundle / 'resources' / name)
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, bundle / name)
    shutil.copy2(ROOT / 'config.ini.example', bundle / 'config.ini')
    shutil.copy2(ROOT / 'config.ini.example', bundle / 'config.ini.example')
    shutil.copytree(ROOT / 'docs' / 'images', bundle / 'docs' / 'images')
    python_dll = Path(sys.base_prefix) / 'python3.dll'
    if python_dll.exists():
        shutil.copy2(python_dll, bundle / 'engine' / 'python3.dll')
    licenses = bundle / 'licenses'
    for package in ('PySide6-Essentials', 'shiboken6'):
        distribution = importlib.metadata.distribution(package)
        for item in distribution.files or []:
            if 'licenses' in item.parts and distribution.locate_file(item).is_file():
                destination = licenses / package / Path(*item.parts[item.parts.index('licenses') + 1:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(item), destination)
    forbidden = {'cookies.json', 'activities.json', 'QRcode.jpg'}
    assert not any(p.name in forbidden for p in bundle.rglob('*'))
    assert not (bundle / 'logs').exists()
    from scripts.smoke_windows import check_bundle
    check_bundle(bundle)
    archive = shutil.make_archive(str(ROOT / 'dist' / f'Autovisor-{__version__}-windows-amd64'), 'zip', ROOT / 'dist', 'Autovisor')
    digest = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    (ROOT / 'dist' / 'SHA256SUMS.txt').write_text(f'{digest}  {Path(archive).name}\n', encoding='utf-8')
    print(f'发行包：{archive}')


if __name__ == '__main__':
    main()
