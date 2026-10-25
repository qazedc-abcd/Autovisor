# encoding=utf-8
import configparser
import json
import os
import re
import sys


DEFAULT_MIRRORS = {
    "Huawei": "https://mirrors.huaweicloud.com/repository/pypi",
    "Aliyun": "https://mirrors.aliyun.com/pypi",
    "Tsinghua": "https://pypi.tuna.tsinghua.edu.cn",
    "Official": "https://pypi.org",
}


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, config_path=None, mirrors_path=None):
        self.config_path = config_path
        self._config = configparser.ConfigParser(interpolation=None)
        if config_path:
            # 用户常量
            self._read_config()
            self.driver = self.get_driver()
            self.username = self._config.get('user-account', 'username', raw=True)
            self.password = self._config.get('user-account', 'password', raw=True)
            # 浏览器选项
            self.exe_path = self._config.get('browser-option', 'EXE_PATH', raw=True)
            self.attach_existing_chrome = self.get_bool_field('browser-option', 'attachExistingChrome', fallback=False)
            self.cdp_url = self._config.get('browser-option', 'cdpUrl', raw=True, fallback='http://127.0.0.1:9222').strip()
            # 脚本选项
            self.enableAutoCaptcha = self.get_bool_field('script-option', 'enableAutoCaptcha')
            self.enableHideWindow = self.get_bool_field('script-option', 'enableHideWindow')
            # 课程选项
            self.soundOff = self.get_bool_field('course-option', 'soundOff')
            self.course_match_rule = re.compile("https://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]")
            self.course_urls = self.get_course_urls()
        self.ai_base_url = self._config.get('ai', 'base_url', raw=True, fallback='https://api.deepseek.com').strip().rstrip('/')
        self.ai_api_key = self._config.get('ai', 'api_key', raw=True, fallback='').strip()
        self.ai_model = self._config.get('ai', 'model', raw=True, fallback='deepseek-flash').strip()
        try:
            self.ai_timeout = self._config.getfloat('ai', 'timeout', fallback=60)
            self.ai_max_retries = self._config.getint('ai', 'max_retries', fallback=2)
            self.qa_questions = self._config.getint('qa', 'questions_per_run', fallback=1)
            self.qa_answers = self._config.getint('qa', 'answers_per_run', fallback=1)
        except ValueError as error:
            raise ConfigError('AI 超时、重试次数及问答数量必须为数字') from error
        if self.ai_timeout <= 0 or min(self.ai_max_retries, self.qa_questions, self.qa_answers) < 0:
            raise ConfigError('AI 超时必须大于零，重试次数和问答数量不能为负数')
        self.video_enabled = self.get_bool_field('video', 'enabled', fallback=True)
        self.qa_enabled = self.get_bool_field('qa', 'enabled')
        self.homework_enabled = self.get_bool_field('homework', 'enabled')
        # 登录
        self.login_url = "https://passport.zhihuishu.com/login"
        self.block_js = '''return document.getElementsByClassName("yidun_jigsaw")[0].src'''
        self.bg_js = '''return document.getElementsByClassName("yidun_bg-img")[0].src'''
        # 弹窗
        self.pop_js = '''document.getElementsByClassName("iconfont iconguanbi")[0].click();'''

        self.mirrors = self._read_mirrors(mirrors_path)

    def _read_config(self) -> None:
        if not os.path.isfile(self.config_path):
            raise ConfigError(f"未找到配置文件: {self.config_path}")
        try:
            self._config.read(self.config_path, encoding='utf-8-sig')
        except UnicodeDecodeError:
            self._config.read(self.config_path, encoding='gbk')
        required_sections = {
            "user-account", "browser-option", "script-option", "course-option", "course-url"
        }
        missing_sections = required_sections - set(self._config.sections())
        if missing_sections:
            raise ConfigError(
                f"配置文件缺少必要配置段: {', '.join(sorted(missing_sections))}"
            )

    def _read_mirrors(self, mirrors_path=None) -> dict:
        if self._config.has_section('mirrors'):
            mirrors = {name: url.strip() for name, url in self._config.items('mirrors', raw=True) if url.strip()}
            if not mirrors:
                raise ConfigError('[mirrors] 至少需要一个镜像源')
            return mirrors
        if not mirrors_path:
            if self.config_path:
                base_dir = os.path.dirname(self.config_path)
            elif getattr(sys, "frozen", False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            mirrors_path = os.path.join(base_dir, "data", "mirrors.json")
        if not os.path.isfile(mirrors_path):
            return DEFAULT_MIRRORS.copy()
        try:
            with open(mirrors_path, "r", encoding="utf-8-sig") as file:
                mirrors = json.load(file)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConfigError(f"镜像配置文件无效: {mirrors_path}") from error
        if not isinstance(mirrors, dict):
            raise ConfigError("镜像配置文件必须是 JSON 对象")
        mirrors = {str(name): str(url).strip() for name, url in mirrors.items() if str(url).strip()}
        if not mirrors:
            raise ConfigError("镜像配置文件至少需要配置一个镜像源")
        # 兼容旧发行包：首次读取后写入唯一用户配置；之后优先使用 INI。
        if self.config_path:
            self._config['mirrors'] = mirrors
            with open(self.config_path, 'w', encoding='utf-8') as file:
                self._config.write(file)
        return mirrors

    def get_driver(self) -> str:
        driver = self._config.get('browser-option', 'driver', raw=True)
        if not driver:
            driver = "edge"
        return driver.lower()

    def get_bool_field(self, section: str, option: str, fallback: bool = False) -> bool:
        field = self._config.get(section, option, raw=True, fallback=str(fallback)).strip().lower()
        if field == "true":
            return True
        else:
            return False

    def get_course_urls(self) -> list:
        course_urls = []
        _options = self._config.options("course-url")
        for _option in _options:
            course_url = self._config.get("course-url", _option, raw=True)
            if not course_url.strip():
                continue
            matched = re.findall(self.course_match_rule, course_url)
            if not matched:
                print(f"\"{course_url.strip()}\"\n不是一个有效网址,将忽略该网址.")
                continue
            course_urls.append(course_url)
        return course_urls

    # @property修饰器可设置属性
    # 这样写可实时响应配置变化
    def _safe_get_float(self, section: str, option: str, default: float = 0.0) -> float:
        try:
            value = self._config.get(section, option, raw=True, fallback='').strip()
            if not value:
                return default
            return float(value)
        except (ValueError, configparser.Error):
            return default

    @property
    def limitMaxTime(self) -> float:
        self._read_config()
        return self._safe_get_float('course-option', 'limitMaxTime', 0.0)

    @property
    def limitSpeed(self) -> float:
        self._read_config()
        speed = self._safe_get_float('course-option', 'limitSpeed', 1.0)
        return min(max(speed, 0.5), 1.8)
