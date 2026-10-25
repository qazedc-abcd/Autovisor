"""检查真正的冻结程序和中文输出，不启动浏览器或课程任务。"""
import configparser
import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment
from PySide6.QtWidgets import QApplication


def check_bundle(bundle):
    bundle = Path(bundle).resolve()
    logs = bundle / 'logs'
    assert not logs.exists(), '请对刚组装的发行目录执行检查，避免混入旧日志。'
    environment = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'QT_QPA_PLATFORM'):
        environment.pop(key, None)
    environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    environment['PYTHONIOENCODING'] = 'utf-8'
    app = QApplication.instance() or QApplication([])
    try:
        with tempfile.TemporaryDirectory() as directory:
            checked = subprocess.run(
                [str(bundle / 'Autovisor.exe'), '--check-ui', '--config', str(bundle / 'config.ini')],
                cwd=directory, env=environment, timeout=30, capture_output=True,
            )
            assert checked.returncode == 0, checked.stderr.decode('utf-8', errors='replace')
            process = QProcess()
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            qenv = QProcessEnvironment()
            for key, value in environment.items():
                qenv.insert(key, value)
            process.setProcessEnvironment(qenv)
            process.setWorkingDirectory(directory)
            process.start(str(bundle / 'AutovisorWorker.exe'), ['--help'])
            if not process.waitForFinished(30000):
                process.kill()
                process.waitForFinished()
                raise AssertionError('冻结工作进程未正常退出。')
            output = bytes(process.readAllStandardOutput()).decode('utf-8')
            assert process.exitCode() == 0 and '配置文件' in output, output
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(bundle / 'config.ini', encoding='utf-8-sig')
            for section in ('video', 'qa', 'homework'):
                parser.set(section, 'enabled', 'False')
            parser.set('script-option', 'enableAutoCaptcha', 'False')
            config = Path(directory) / 'no-tasks.ini'
            with config.open('w', encoding='utf-8') as file:
                parser.write(file)
            checked = subprocess.run(
                [str(bundle / 'AutovisorWorker.exe'), '--config', str(config)],
                cwd=directory, env=environment, timeout=30, capture_output=True,
            )
            output = checked.stdout.decode('utf-8')
            errors = checked.stderr.decode('utf-8')
            assert checked.returncode == 0, output + errors
            assert '程序启动中' in output and '本轮没有需要执行的任务' in output, output
    finally:
        # 仅清理由本次无任务检查生成的日志；不将测试日志装入 ZIP。
        if logs.exists():
            for path in logs.iterdir():
                assert path.is_file() and path.suffix == '.txt'
                path.unlink()
            logs.rmdir()
    print('冻结桌面启动、Qt 管道中文、正常运行中文日志检查通过。')


if __name__ == '__main__':
    check_bundle(Path(__file__).resolve().parents[1] / 'dist' / 'Autovisor')
