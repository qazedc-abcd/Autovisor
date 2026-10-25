"""Material Design 3 风格桌面界面，独立进程运行现有任务。"""
import argparse
import codecs
import configparser
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)
from modules.configs import Config, ConfigError
from modules.desktop_settings import DesktopSettings, application_root, worker_command
from modules.version import __version__

STYLE = """
QWidget {font-family:"Microsoft YaHei UI","Segoe UI";font-size:14px;color:#20372b;}
QMainWindow,QWidget#shell {background:#f5f8f3;}
QFrame#rail {background:#eaf1e8;border-radius:26px;}
QLabel#brand {font-size:26px;font-weight:700;}
QLabel#mark {color:white;background:#245d43;border-radius:19px;font-size:22px;font-weight:700;}
QLabel#eyebrow {color:#526859;font-size:12px;font-weight:600;}
QLabel#display {font-size:33px;font-weight:650;}
QLabel#section {font-size:19px;font-weight:600;}
QLabel#muted {color:#65756a;}
QLabel#status {background:#ddebdc;color:#2b583d;border-radius:15px;padding:7px 14px;}
QLabel#notice {background:#e5eee1;color:#415b43;border-radius:18px;padding:18px;}
QFrame#card {background:white;border:1px solid #e0e8dd;border-radius:24px;}
QFrame#runbar {background:#e4eddf;border-radius:23px;}
QPushButton {background:#e9efe5;border:1px solid transparent;border-radius:20px;padding:11px 20px;min-height:20px;color:#294f37;font-weight:600;}
QPushButton:hover {background:#dce8d8;}
QPushButton:pressed {background:#ccdec7;}
QPushButton:disabled {color:#91a08f;background:#e9eee6;}
QPushButton#primary {background:#245d43;color:white;padding:13px 26px;border-radius:23px;}
QPushButton#primary:hover {background:#174b32;}
QPushButton#primary:disabled {background:#afc2b0;}
QPushButton#stop {background:#ffdad6;color:#8b302b;}
QPushButton#nav {background:transparent;text-align:left;border-radius:23px;padding:15px 19px;font-weight:500;}
QPushButton#nav:checked {background:#cce8c9;color:#183c26;font-weight:650;}
QPushButton#mode {background:white;color:#4b6252;border:1px solid #d9e4d6;border-radius:24px;text-align:left;padding:23px;font-size:17px;}
QPushButton#mode:checked {background:#d4edcf;color:#183e25;border:2px solid #477a4b;}
QPushButton#mode:hover {background:#e6f1e0;}
QLineEdit,QPlainTextEdit,QComboBox,QSpinBox,QDoubleSpinBox {background:#f3f6f0;border:1px solid #d5dfd0;border-radius:12px;padding:11px 13px;selection-background-color:#c4e6bf;}
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus {border:2px solid #477a4b;}
QComboBox::drop-down {border:0;width:30px;}
QScrollArea {border:0;background:transparent;}
QScrollArea>QWidget>QWidget {background:transparent;}
QScrollBar:vertical {width:8px;background:transparent;margin:10px 0;}
QScrollBar::handle:vertical {background:#c6d3c0;border-radius:4px;min-height:36px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QCheckBox {spacing:10px;padding:7px 0;}
QCheckBox::indicator {width:19px;height:19px;border:1px solid #71866e;border-radius:5px;background:white;}
QCheckBox::indicator:checked {background:#245d43;border:3px solid #a4c99b;}
QPlainTextEdit#console {background:#edf3e9;border:0;font-family:"Consolas","Microsoft YaHei UI";font-size:12px;padding:16px;}
"""


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def card(title, description=''):
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(25, 23, 25, 24)
    layout.setSpacing(16)
    layout.addWidget(label(title, 'section'))
    if description:
        layout.addWidget(label(description, 'muted', True))
    return frame, layout


class MainWindow(QMainWindow):
    def __init__(self, config_path=None):
        super().__init__()
        self.root = application_root()
        self.settings = DesktopSettings(config_path or self.root / 'config.ini')
        self.fields = {}
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.stopping = False
        self.setWindowTitle('Autovisor · 课程助手')
        self.setWindowIcon(QIcon(str(self.root / 'resources' / 'zhs.ico')))
        self.resize(1200, 810)
        self.setMinimumSize(1000, 680)
        self.setStyleSheet(STYLE)
        shell = QWidget()
        shell.setObjectName('shell')
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(18, 18, 26, 18)
        outer.setSpacing(30)
        self.setCentralWidget(shell)
        rail = QFrame()
        rail.setObjectName('rail')
        rail.setFixedWidth(210)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(19, 27, 19, 23)
        mark = label('A', 'mark')
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(46, 46)
        rail_layout.addWidget(mark)
        rail_layout.addSpacing(16)
        rail_layout.addWidget(label('Autovisor', 'brand'))
        rail_layout.addWidget(label('把课程安排得更清楚', 'muted'))
        rail_layout.addSpacing(35)
        self.stack = QStackedWidget()
        self.navigation = []
        for index, title in enumerate(['任务概览', '课程与账号', 'AI 设置', '运行记录']):
            button = QPushButton(title)
            button.setObjectName('nav')
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.navigate(i))
            rail_layout.addWidget(button)
            self.navigation.append(button)
        rail_layout.addStretch()
        rail_layout.addWidget(label('正式考试始终排除', 'muted', True))
        rail_layout.addWidget(label(f'桌面版 {__version__} · MD3', 'eyebrow'))
        outer.addWidget(rail)
        content = QVBoxLayout()
        content.setSpacing(18)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(label('YOUR LEARNING SPACE', 'eyebrow'))
        heading.addWidget(label('今天，按你的节奏来', 'display'))
        header.addLayout(heading)
        header.addStretch()
        self.status = label('准备就绪', 'status')
        header.addWidget(self.status, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(header)
        content.addWidget(self.stack, 1)
        self.build_overview()
        self.build_courses()
        self.build_ai()
        self.build_logs()
        runbar = QFrame()
        runbar.setObjectName('runbar')
        run_layout = QHBoxLayout(runbar)
        run_layout.setContentsMargins(22, 15, 17, 15)
        self.summary = label('', 'muted')
        run_layout.addWidget(self.summary, 1)
        self.save_button = QPushButton('保存设置')
        self.save_button.clicked.connect(self.save)
        self.start_button = QPushButton('开始任务')
        self.start_button.setObjectName('primary')
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton('停止任务')
        self.stop_button.setObjectName('stop')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        for button in (self.save_button, self.stop_button, self.start_button):
            run_layout.addWidget(button)
        content.addWidget(runbar)
        outer.addLayout(content, 1)
        self.navigate(0)
        self.update_summary()

    def scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 10, 4)
        layout.setSpacing(18)
        scroll.setWidget(page)
        self.stack.addWidget(scroll)
        return layout

    def build_overview(self):
        layout = self.scroll_page()
        layout.addWidget(label('选择这一次要做什么', 'section'))
        layout.addWidget(label('三个功能独立运行。可以只选一个，也可以组合。', 'muted'))
        modes = QHBoxLayout()
        self.modes = {}
        for section, title, note in [
            ('video', '刷课', '继续学习课程视频'), ('qa', '课程互动', '先查分，再提问与回答'),
            ('homework', '作业答题', '只处理未提交的作业'),
        ]:
            button = QPushButton(f'{title}\n\n{note}')
            button.setObjectName('mode')
            button.setMinimumHeight(146)
            button.setCheckable(True)
            button.setChecked(self.settings.enabled(section, default=section == 'video'))
            button.toggled.connect(self.update_summary)
            self.modes[section] = button
            modes.addWidget(button, 1)
        layout.addLayout(modes)
        layout.addWidget(label('放心掌握运行范围\n互动满分会跳过；作业与考试按类型区分，尚未开放的考试也不会进入。', 'notice', True))
        frame, inner = card('开始前，只需要三步')
        for title, note in [
            ('01   添加课程', '在「课程与账号」粘贴课程学习页链接，每行一门课。'),
            ('02   按需填写 AI', '开启互动或作业时，到「AI 设置」填写自己的 API 密钥。'),
            ('03   点击开始任务', '浏览器打开后完成登录；遇到可见安全验证，按页面提示手动处理。'),
        ]:
            inner.addWidget(label(title, 'section'))
            inner.addWidget(label(note, 'muted', True))
        layout.addWidget(frame)
        layout.addStretch()

    def field(self, form, title, section, key, default='', secret=False, number=None):
        value = self.settings.get(section, key, default)
        if number:
            widget = QDoubleSpinBox() if number == 'decimal' else QSpinBox()
            widget.setRange(0.5 if key == 'limitSpeed' else 0, 1.8 if key == 'limitSpeed' else 9999)
            if number == 'decimal':
                widget.setDecimals(1)
                widget.setSingleStep(0.1)
            if key == 'timeout':
                widget.setMinimum(1)
            widget.setValue(float(value or default) if number == 'decimal' else int(value or default))
        else:
            widget = QLineEdit(value)
            if secret:
                widget.setEchoMode(QLineEdit.EchoMode.Password)
        widget.setAccessibleName(title)
        form.addRow(title, widget)
        self.fields[(section, key)] = widget
        return widget

    def check(self, layout, title, section, key, default=False):
        widget = QCheckBox(title)
        widget.setChecked(self.settings.enabled(section, key, default))
        layout.addWidget(widget)
        self.fields[(section, key)] = widget

    def build_courses(self):
        layout = self.scroll_page()
        frame, inner = card('课程链接', '打开智慧树课程的学习页面，复制浏览器地址栏中的完整链接。每行一门课。')
        self.courses = QPlainTextEdit(self.settings.courses())
        self.courses.setPlaceholderText('粘贴课程学习页的完整链接，一行一门课')
        self.courses.setMinimumHeight(122)
        self.courses.setAccessibleName('课程链接')
        self.courses.textChanged.connect(self.update_summary)
        inner.addWidget(self.courses)
        layout.addWidget(frame)
        frame, inner = card('登录与浏览器', '账号、密码可留空，开始后在浏览器中手动登录。密码与 API 密钥保存在本机 config.ini，请妥善保管。')
        form = QFormLayout()
        form.setSpacing(13)
        self.field(form, '智慧树账号', 'user-account', 'username')
        self.field(form, '登录密码', 'user-account', 'password', secret=True)
        driver = QComboBox()
        driver.addItems(['Edge', 'Chrome'])
        driver.setCurrentText(self.settings.get('browser-option', 'driver', 'Edge').capitalize())
        form.addRow('使用浏览器', driver)
        self.fields[('browser-option', 'driver')] = driver
        self.field(form, '浏览器路径（可留空）', 'browser-option', 'EXE_PATH')
        inner.addLayout(form)
        layout.addWidget(frame)
        frame, inner = card('视频播放', '每门课分别计时。填 0 表示不限时；播放速度范围为 0.5–1.8。')
        form = QFormLayout()
        form.setSpacing(13)
        self.field(form, '每课时长 / 分钟', 'course-option', 'limitMaxTime', '30', number='decimal')
        self.field(form, '播放速度', 'course-option', 'limitSpeed', '1.0', number='decimal')
        inner.addLayout(form)
        self.check(inner, '静音播放', 'course-option', 'soundOff', True)
        self.check(inner, '启动后最小化浏览器', 'script-option', 'enableHideWindow')
        layout.addWidget(frame)
        layout.addStretch()

    def build_ai(self):
        layout = self.scroll_page()
        frame, inner = card('连接你的 AI 服务', '支持 OpenAI 兼容接口。密钥由你自行填写，软件不提供共享密钥。')
        form = QFormLayout()
        form.setSpacing(13)
        self.field(form, '服务地址', 'ai', 'base_url', 'https://api.deepseek.com')
        self.field(form, 'API 密钥', 'ai', 'api_key', secret=True)
        self.field(form, '模型名称', 'ai', 'model', 'deepseek-flash')
        self.field(form, '请求超时 / 秒', 'ai', 'timeout', '60', number='integer')
        self.field(form, '最大重试次数', 'ai', 'max_retries', '2', number='integer')
        inner.addLayout(form)
        layout.addWidget(frame)
        frame, inner = card('课程互动', '先查实际得分，满分就跳过。得分通常次日更新；数量同时作为每课当天上限，填 0 可关闭对应能力。')
        form = QFormLayout()
        form.setSpacing(13)
        self.field(form, '每课提问数量', 'qa', 'questions_per_run', '1', number='integer')
        self.field(form, '每课回答数量', 'qa', 'answers_per_run', '1', number='integer')
        inner.addLayout(form)
        layout.addWidget(frame)
        layout.addWidget(label('关于作业\n支持单选、多选和判断题。题干无法直接读取时会使用题目小截图，需要模型支持图片输入。AI 可能答错，请留意平台返回成绩。', 'notice', True))
        layout.addStretch()

    def build_logs(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        frame, inner = card('运行记录', '登录提示、当前课程和结果显示在这里。完整日志在软件目录的 logs 文件夹。')
        tools = QHBoxLayout()
        open_logs = QPushButton('打开日志文件夹')
        open_logs.clicked.connect(self.open_logs)
        readme = QPushButton('阅读使用说明')
        readme.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root / 'README.md'))))
        tools.addWidget(open_logs)
        tools.addWidget(readme)
        tools.addStretch()
        inner.addLayout(tools)
        self.console = QPlainTextEdit()
        self.console.setObjectName('console')
        self.console.setReadOnly(True)
        self.console.setPlaceholderText('准备好后点击「开始任务」。\n\n需要你操作时，请切到浏览器完成登录或安全验证。')
        self.console.document().setMaximumBlockCount(2500)
        inner.addWidget(self.console, 1)
        layout.addWidget(frame, 1)
        self.stack.addWidget(page)

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.navigation):
            button.setChecked(i == index)

    def update_summary(self, *args):
        if not hasattr(self, 'summary'):
            return
        count = len([s for s in self.courses.toPlainText().splitlines() if s.strip()])
        modes = [title for key, title in [('video', '刷课'), ('qa', '互动'), ('homework', '作业')] if self.modes[key].isChecked()]
        self.summary.setText(f'{count} 门课程   ·   ' + (' / '.join(modes) if modes else '尚未选择功能'))

    def save(self):
        fields = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QComboBox):
                value = widget.currentText()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                value = widget.value()
            else:
                value = widget.text() if key == ('user-account', 'password') else widget.text().strip()
            fields[key] = value
        fields.update({(key, 'enabled'): button.isChecked() for key, button in self.modes.items()})
        try:
            self.settings.save(fields, self.courses.toPlainText())
            Config(self.settings.path)
        except (OSError, ValueError, ConfigError) as error:
            QMessageBox.warning(self, '设置没有保存成功', str(error))
            return False
        self.status.setText('设置已保存')
        self.update_summary()
        return True

    def start(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        if not any(b.isChecked() for b in self.modes.values()):
            QMessageBox.information(self, '先选择一个功能', '请开启刷课、互动或作业。')
            self.navigate(0)
            return
        if not self.courses.toPlainText().strip():
            QMessageBox.information(self, '还没有添加课程', '请先添加至少一门课程学习页的链接。')
            self.navigate(1)
            return
        if not self.save():
            return
        if (self.modes['qa'].isChecked() or self.modes['homework'].isChecked()) and not self.settings.get('ai', 'api_key').strip():
            QMessageBox.information(self, '还没有填写 AI 密钥', '请填写自己的 API 密钥，或只开启刷课。')
            self.navigate(2)
            return
        program, arguments = worker_command(self.settings.path)
        if not Path(program).is_file():
            QMessageBox.warning(self, '工作程序缺失', '请完整解压发行包，保留 AutovisorWorker.exe 和随附文件夹。')
            return
        self.stopping = False
        self.decoder.reset()
        self.console.clear()
        self.navigate(3)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONIOENCODING', 'utf-8')
        environment.insert('PYTHONUNBUFFERED', '1')
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(self.root))
        self.start_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText('正在运行')
        self.process.start(program, arguments)

    def read_output(self):
        text = self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text).replace('\r', '\n')
        for key in [('ai', 'api_key'), ('user-account', 'password'), ('user-account', 'username')]:
            secret = self.settings.get(*key).strip()
            if secret:
                text = text.replace(secret, '••••')
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.console.setTextCursor(cursor)
        self.console.ensureCursorVisible()

    def finished(self, exit_code, exit_status):
        self.read_output()
        self.start_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status.setText('已停止' if self.stopping else '任务完成' if exit_code == 0 else '有任务未完成')

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.console.appendPlainText('启动失败。请完整解压发行包后重试。')
            self.finished(1, None)

    def stop(self):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.stopping = True
        self.status.setText('正在停止')
        self.process.terminate()
        QTimer.singleShot(3000, self.kill_if_running)

    def kill_if_running(self):
        if self.stopping and self.process.state() != QProcess.ProcessState.NotRunning:
            if sys.platform == 'win32':
                subprocess.run(
                    ['taskkill', '/PID', str(self.process.processId()), '/T', '/F'],
                    creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=10,
                )
            else:
                self.process.kill()

    def open_logs(self):
        folder = self.root / 'logs'
        folder.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def closeEvent(self, event):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            answer = QMessageBox.question(self, '任务仍在运行', '关闭窗口会停止当前任务。确定关闭吗？')
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stopping = True
            self.kill_if_running()
            self.process.waitForFinished(3000)
        event.accept()


def main():
    parser = argparse.ArgumentParser(description='Autovisor 桌面版')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--check-ui', action='store_true', help='只检查桌面窗口能否启动，不运行课程任务')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('Autovisor')
    app.setStyle('Fusion')
    try:
        window = MainWindow(args.config)
    except (OSError, ValueError, configparser.Error) as error:
        QMessageBox.critical(None, '无法读取设置', f'请检查 config.ini。\n{error}')
        return 1
    window.show()
    if args.check_ui:
        QTimer.singleShot(500, app.quit)
    return app.exec()
