"""工作进程与桌面窗口约定使用 UTF-8，不依赖系统代码页或冻结程序的环境变量处理。"""
import sys


def configure_utf8_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            reconfigure(encoding='utf-8', errors='replace')
