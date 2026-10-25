# 开发与构建

源码需要 Python 3.10–3.13。Windows 发行版自带运行环境。

## 源码运行

    uv sync --extra gui --extra captcha
    uv run python GUI.py

命令行入口仍为 Autovisor.py，支持 --config、--check-browser 和 --check-course。
桌面通过 QProcess 启动独立工作进程，接收 UTF-8 输出，课程逻辑不占用 UI 线程。

## 测试

    uv run python -m unittest discover -s tests -v
    uv run python GUI.py --check-ui --config config.ini.example

GUI 测试使用临时配置和模拟工作进程，不连接真实课堂或付费 AI。
业务回归覆盖满分跳过、考试排除、题号映射、异步切题、漏答拦截和失败状态。

## Windows 打包

    uv run --with pyinstaller python build.py

需先安装 gui 和 captcha 依赖。产物为 dist 下的 ZIP 和 SHA256SUMS.txt。
Autovisor.exe 为桌面界面，AutovisorWorker.exe 为工作进程，运行库分别位于 internal 和 engine。
采用白名单组装，只复制空白配置、必要资源、使用说明和第三方许可证。
不得将开发者配置、登录数据、日志、自动状态或私人测试证据放进发行包。
