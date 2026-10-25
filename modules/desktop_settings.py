"""桌面端与命令行共用同一个 INI。"""
import configparser
import shutil
import sys
from pathlib import Path
from modules.configs import Config


def application_root():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]


class DesktopSettings:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            shutil.copyfile(application_root() / 'config.ini.example', self.path)
        self.parser = configparser.ConfigParser(interpolation=None)
        self.parser.read(self.path, encoding='utf-8-sig')

    def get(self, section, key, default=''):
        return self.parser.get(section, key, raw=True, fallback=str(default))

    def enabled(self, section, key='enabled', default=False):
        return self.get(section, key, default).strip().lower() == 'true'

    def courses(self):
        if not self.parser.has_section('course-url'):
            return ''
        return '\n'.join(v.strip() for _, v in self.parser.items('course-url', raw=True) if v.strip())

    def save(self, fields, courses):
        for (section, key), value in fields.items():
            if not self.parser.has_section(section):
                self.parser.add_section(section)
            self.parser.set(section, key, str(value))
        self.parser['course-url'] = {
            f'URL{i}': line.strip() for i, line in enumerate(courses.splitlines(), 1) if line.strip()
        }
        self.parser.remove_option('script-option', 'showDonateCode')
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as file:
            self.parser.write(file)
        Config(temporary)
        temporary.replace(self.path)


def worker_command(config_path):
    root = application_root()
    if getattr(sys, 'frozen', False):
        return str(root / 'AutovisorWorker.exe'), ['--config', str(config_path)]
    return sys.executable, ['-u', str(root / 'Autovisor.py'), '--config', str(config_path)]
