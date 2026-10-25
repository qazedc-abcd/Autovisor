import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import Autovisor
from modules.configs import Config


class RunModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_activity_only_never_opens_video_or_starts_video_tasks(self):
        for qa, homework, completed in ((True, False, True), (False, True, True), (True, True, True), (False, True, False)):
            with self.subTest(qa=qa, homework=homework, completed=completed):
                config = SimpleNamespace(
                    video_enabled=False, qa_enabled=qa, homework_enabled=homework,
                    enableAutoCaptcha=False, enableHideWindow=False,
                    course_urls=['course-one', 'course-two'], showDonateCode=False,
                )
                page, context, logger = AsyncMock(), Mock(), Mock()
                context.browser.close = AsyncMock()
                manager = AsyncMock()
                with patch.object(Autovisor, 'logger', logger, create=True), \
                        patch.object(Autovisor, 'async_playwright', return_value=manager), \
                        patch.object(Autovisor, 'load_cookies', return_value=None), \
                        patch.object(Autovisor, 'init_page', AsyncMock(return_value=(page, context))), \
                        patch.object(Autovisor, 'ensure_login', AsyncMock(return_value=False)), \
                        patch.object(Autovisor, 'persist_login_cookies', AsyncMock()), \
                        patch.object(Autovisor, 'run_course_activities', AsyncMock(return_value=completed)) as activities, \
                        patch.object(Autovisor, 'detect_catalog_after_verification', AsyncMock()) as detect, \
                        patch.object(Autovisor, 'run_course', AsyncMock()) as video, \
                        patch.object(Autovisor, 'play_video', AsyncMock()) as play, \
                        patch.object(Autovisor, 'video_optimize', AsyncMock()) as optimize, \
                        patch.object(Autovisor, 'skip_questions', AsyncMock()) as skip, \
                        patch.object(Autovisor, 'wait_for_verify', AsyncMock()) as verify, \
                        patch.object(Autovisor, 'task_monitor', AsyncMock()) as monitor:
                    self.assertEqual(await Autovisor.main(config), completed)
                self.assertEqual(activities.await_args_list, [
                    call(page, config, logger, course_url='course-one'),
                    call(page, config, logger, course_url='course-two'),
                ])
                page.goto.assert_not_awaited()
                for operation in (detect, video, play, optimize, skip, verify, monitor):
                    operation.assert_not_called()

    async def test_all_modes_off_does_not_start_browser(self):
        config = SimpleNamespace(video_enabled=False, qa_enabled=False, homework_enabled=False)
        with patch.object(Autovisor, 'logger', Mock(), create=True), \
                patch.object(Autovisor, 'async_playwright') as browser:
            self.assertTrue(await Autovisor.main(config))
        browser.assert_not_called()


class ModeConfigTests(unittest.TestCase):
    def test_independent_modes_and_legacy_video_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.ini'
            example = Path('config.ini.example').read_text(encoding='utf-8-sig')
            path.write_text(example, encoding='utf-8')
            original = Config(path)
            self.assertTrue(original.video_enabled)
            self.assertFalse(original.qa_enabled)
            self.assertFalse(original.homework_enabled)
            parser = original._config
            parser.remove_section('video')
            with path.open('w', encoding='utf-8') as file:
                parser.write(file)
            self.assertTrue(Config(path).video_enabled)
            parser['video'] = {'enabled': 'False'}
            parser.set('qa', 'enabled', 'True')
            for homework in ('True', 'False'):
                parser.set('homework', 'enabled', homework)
                with path.open('w', encoding='utf-8') as file:
                    parser.write(file)
                current = Config(path)
                self.assertEqual((current.video_enabled, current.qa_enabled, current.homework_enabled),
                                 (False, True, homework == 'True'))
