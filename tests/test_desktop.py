import configparser
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from modules.configs import Config, ConfigError
from modules.desktop_settings import DesktopSettings, worker_command


class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'config.ini'
        shutil.copyfile('config.ini.example', self.path)

    def test_settings_preserve_raw_keys_and_unknown_options(self):
        settings = DesktopSettings(self.path)
        settings.parser['custom'] = {'keep': 'unchanged'}
        settings.parser.set('script-option', 'showDonateCode', 'True')
        settings.save({
            ('video', 'enabled'): False, ('qa', 'enabled'): True,
            ('homework', 'enabled'): True, ('ai', 'api_key'): 'test%key',
        }, 'https://studyvideoh5.zhihuishu.com/a\n\nhttps://studyvideoh5.zhihuishu.com/b')
        current = Config(self.path)
        self.assertEqual(current.ai_api_key, 'test%key')
        self.assertEqual((current.video_enabled, current.qa_enabled, current.homework_enabled), (False, True, True))
        self.assertEqual(len(current.course_urls), 2)
        self.assertEqual(current._config.get('custom', 'keep'), 'unchanged')
        self.assertFalse(current._config.has_option('script-option', 'showDonateCode'))

    def test_invalid_settings_do_not_replace_existing_config(self):
        original = self.path.read_bytes()
        with self.assertRaises(ConfigError):
            DesktopSettings(self.path).save({('ai', 'timeout'): 0}, '')
        self.assertEqual(self.path.read_bytes(), original)

    def test_frozen_worker_uses_packaged_executable_and_explicit_config(self):
        with patch('modules.desktop_settings.sys.frozen', True, create=True), \
                patch('modules.desktop_settings.sys.executable', str(Path(self.directory.name) / 'Autovisor.exe')):
            program, arguments = worker_command(self.path)
        self.assertEqual(Path(program).name, 'AutovisorWorker.exe')
        self.assertEqual(arguments, ['--config', str(self.path)])


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'GUI extra not installed')
class DesktopWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_three_modes_save_through_real_window_without_starting_tasks(self):
        from modules.desktop_ui import MainWindow
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.ini'
            shutil.copyfile('config.ini.example', path)
            window = MainWindow(path)
            self.addCleanup(window.close)
            window.modes['video'].setChecked(False)
            window.modes['qa'].setChecked(False)
            window.modes['homework'].setChecked(True)
            window.fields[('ai', 'api_key')].setText('test%key')
            window.courses.setPlainText('https://studyvideoh5.zhihuishu.com/example')
            with patch.object(window.process, 'start') as start:
                self.assertTrue(window.save())
                start.assert_not_called()
            loaded = Config(path)
            self.assertFalse(loaded.video_enabled)
            self.assertTrue(loaded.homework_enabled)
            self.assertEqual(loaded.ai_api_key, 'test%key')

    def test_no_modes_selected_never_launches_worker(self):
        from modules.desktop_ui import MainWindow, QMessageBox
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.ini'
            shutil.copyfile('config.ini.example', path)
            window = MainWindow(path)
            self.addCleanup(window.close)
            for mode in window.modes.values():
                mode.setChecked(False)
            with patch.object(window.process, 'start') as start, patch.object(QMessageBox, 'information'):
                window.start()
            start.assert_not_called()

    def test_mode_checkboxes_remain_visible_on_all_pages_in_small_window(self):
        from PySide6.QtWidgets import QCheckBox
        from modules.desktop_ui import MainWindow
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.ini'
            shutil.copyfile('config.ini.example', path)
            window = MainWindow(path)
            self.addCleanup(window.close)
            window.show()
            for width, height in ((800, 560), (760, 480)):
                window.resize(width, height)
                for page in range(window.stack.count()):
                    window.navigate(page)
                    self.app.processEvents()
                    self.assertLessEqual(window.width(), width)
                    self.assertLessEqual(window.height(), height)
                    for checkbox in window.modes.values():
                        self.assertIsInstance(checkbox, QCheckBox)
                        self.assertTrue(checkbox.isVisible())
                        self.assertTrue(window.rect().contains(checkbox.mapTo(window, checkbox.rect().bottomRight())))
                    self.assertTrue(window.rect().contains(window.start_button.mapTo(window, window.start_button.rect().bottomRight())))

    def test_chinese_log_split_inside_utf8_character_is_rendered_correctly(self):
        from modules.desktop_ui import MainWindow
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.ini'
            shutil.copyfile('config.ini.example', path)
            window = MainWindow(path)
            self.addCleanup(window.close)
            raw = '课程日志：正在学习，已保存答案。\n'.encode('utf-8')
            pieces = [raw[:2], raw[2:7], raw[7:11], raw[11:]]
            with patch.object(window.process, 'readAllStandardOutput', side_effect=pieces):
                for _ in pieces:
                    window.read_output()
            self.assertEqual(window.console.toPlainText(), '课程日志：正在学习，已保存答案。\n')
