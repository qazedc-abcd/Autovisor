import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeout

from modules.activity_state import ActivityState
from modules.ai_client import AIClient, AIError
from modules.configs import Config, DEFAULT_MIRRORS
from modules.course_activities import parse_score, read_interaction_score, run_course_activities, run_qa_if_needed, wait_activity_content
from modules.homework import CURRENT_QUESTION, complete_homework, fill_question, is_homework_card, map_answer, read_question, select_question, solve_question, submitted
from modules.qa import confirm_post, open_qa_home, personal_records, publish, run_qa, wait_questions


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_score_skips_ai_and_all_qa_even_with_non_five_maximum(self):
        page, ai = AsyncMock(), AsyncMock()
        with patch('modules.course_activities.read_interaction_score', AsyncMock(return_value=(8, 8))), \
                patch('modules.qa.run_qa', AsyncMock()) as qa:
            await run_qa_if_needed(page, {'report': 'report'}, SimpleNamespace(), Mock(), Mock(), ai)
        qa.assert_not_awaited()
        ai.generate.assert_not_awaited()
        page.goto.assert_awaited_once_with('report')

    async def test_score_waits_past_zero_over_zero(self):
        page = Mock()
        page.locator.return_value.click = AsyncMock()
        page.locator.return_value.all_text_contents = AsyncMock(side_effect=[['0', '0'], ['2', '8']])
        with patch('modules.course_activities.wait_verification', AsyncMock()), \
                patch('modules.course_activities.asyncio.sleep', AsyncMock()):
            self.assertEqual(await read_interaction_score(page, Mock()), (2, 8))
        for values in [('0', '0'), ('', '5'), ('nan', '5'), ('1', 'inf')]:
            self.assertIsNone(parse_score(*values))

    async def test_unknown_score_never_calls_ai_or_qa(self):
        page, ai = AsyncMock(), AsyncMock()
        with patch('modules.course_activities.read_interaction_score', AsyncMock(side_effect=RuntimeError('not loaded'))), \
                patch('modules.qa.run_qa', AsyncMock()) as qa:
            with self.assertRaises(RuntimeError):
                await run_qa_if_needed(page, {'report': 'report'}, SimpleNamespace(), Mock(), Mock(), ai)
        qa.assert_not_awaited()
        ai.generate.assert_not_awaited()

    async def test_pending_publish_survives_restart_and_next_day_without_reposting(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'state.json'
            state = ActivityState(path, 'course')
            state.begin('question', 'key', 'some question?', 'qa')
            self.assertEqual(ActivityState(path, 'course').remaining('question', 1), 0)
            state.records['posts'][0]['day'] = '2026-01-01'
            state.save()
            loaded, ai = ActivityState(path, 'course'), AsyncMock()
            with patch('modules.qa.confirm_post', AsyncMock(return_value=False)):
                with self.assertRaisesRegex(RuntimeError, '上次发布'):
                    await run_qa(AsyncMock(), {}, SimpleNamespace(), Mock(), loaded, ai)
            ai.generate.assert_not_awaited()

    async def test_answer_cache_remaps_shuffled_options_without_second_ai_call(self):
        with tempfile.TemporaryDirectory() as root:
            state, ai = ActivityState(Path(root) / 'state.json', 'course'), AsyncMock()
            ai.generate.return_value = {'question_id': '1', 'choices': ['乙']}
            question = {'id': '1', 'type': '单选题', 'text': '题干', 'image': None,
                        'options': [{'text': '甲'}, {'text': '乙'}]}
            course = {'title': '课程', 'chapters': ['章节']}
            self.assertEqual(await solve_question(question, course, state, ai), [1])
            question['id'] = '3'
            question['options'].reverse()
            self.assertEqual(await solve_question(question, course, state, ai), [0])
            self.assertEqual(ai.generate.await_count, 1)

    async def test_existing_today_posts_cap_both_abilities(self):
        item = Mock()
        item.locator.return_value.inner_text = AsyncMock(return_value=date.today().isoformat())
        state = Mock()
        state.records = {'posts': []}
        state.remaining.return_value = 1
        ai = AsyncMock()
        with patch('modules.qa.personal_records', AsyncMock(return_value=[item])):
            await run_qa(AsyncMock(), {}, SimpleNamespace(qa_answers=1, qa_questions=1), Mock(), state, ai)
        ai.generate.assert_not_awaited()

    async def test_same_answer_on_different_question_does_not_confirm_a_post(self):
        item = Mock()
        item.locator.return_value.inner_text = AsyncMock(return_value='其他问题')
        with patch('modules.qa.personal_records', AsyncMock(return_value=[item])):
            self.assertFalse(await confirm_post(AsyncMock(), {},
                             {'kind': 'answer', 'text': '同样的回答', 'question': '目标问题'}))

    async def test_editor_that_drops_text_never_clicks_publish_or_records_pending(self):
        page, state = Mock(), Mock()
        page.locator.return_value.click = AsyncMock()
        dialog = page.get_by_role.return_value
        dialog.locator.return_value.fill = AsyncMock()
        dialog.locator.return_value.input_value = AsyncMock(return_value='')
        with patch('modules.qa.wait_verification', AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, '未保留完整正文'):
                await publish(page, {}, 'question', 'key', '课程问题？', state, Mock())
        state.begin.assert_not_called()
        dialog.get_by_text.assert_not_called()

    async def test_activity_content_waits_for_async_business_data(self):
        page = Mock(url='https://example.test/home')
        page.locator.return_value.first.wait_for = AsyncMock(side_effect=[PlaywrightTimeout('loading'), None])
        with patch('modules.course_activities.has_visible_verification', AsyncMock(return_value=False)):
            await wait_activity_content(page, '.answerCard_list1 li')
        self.assertEqual(page.locator.return_value.first.wait_for.await_count, 2)

    async def test_return_to_qa_uses_observed_breadcrumb_without_reloading(self):
        page = Mock(url='https://qah5.zhihuishu.com/qa.html#/web/myJoin/course')
        back = page.locator.return_value.get_by_text.return_value
        back.count = AsyncMock(return_value=1)
        back.click = AsyncMock()
        page.goto = AsyncMock()
        await open_qa_home(page, {'qa': 'home'})
        back.click.assert_awaited_once()
        page.goto.assert_not_awaited()

    async def test_personal_tab_switch_stays_in_same_document(self):
        page = Mock(url='https://qah5.zhihuishu.com/qa.html#/web/myJoin/course')
        page.goto = AsyncMock()
        page.locator.return_value.filter.return_value.click = AsyncMock()
        page.locator.return_value.wait_for = AsyncMock()
        page.locator.return_value.inner_text = AsyncMock(return_value='没有更多了')
        page.locator.return_value.all = AsyncMock(return_value=[])
        with patch('modules.qa.wait_activity_content', AsyncMock()):
            self.assertEqual(await personal_records(page, {}, 'answer'), [])
        page.goto.assert_not_awaited()

    async def test_stalled_readonly_question_list_reloads_only_once(self):
        page = Mock()
        page.reload = AsyncMock()
        with patch('modules.qa.wait_activity_content', AsyncMock(side_effect=[RuntimeError('loading'), None])) as wait:
            await wait_questions(page, Mock())
        self.assertEqual(wait.await_count, 2)
        page.reload.assert_awaited_once()

    async def test_homework_waits_for_question_numbers_before_counting(self):
        page = Mock()
        loaded = False
        numbers = Mock()

        async def count():
            return 5 if loaded else 0

        async def ready(*args):
            nonlocal loaded
            loaded = True

        numbers.count = count
        box = Mock()
        box.wait_for = AsyncMock()
        box.get_by_role.return_value.click = AsyncMock()
        page.locator.side_effect = lambda selector: numbers if selector == '.answerCard_list1 li' else SimpleNamespace(filter=lambda **kw: box)
        page.get_by_role.return_value.click = AsyncMock()
        page.get_by_role.return_value.wait_for = AsyncMock()
        page.get_by_text.return_value.wait_for = AsyncMock()
        questions = [{'id': str(i), 'image': None, 'type': '单选题', 'options': [{'text': '选项'}]}
                     for i in range(1, 6)]
        with patch('modules.homework.wait_activity_content', side_effect=ready), \
                patch('modules.homework.wait_verification', AsyncMock()), \
                patch('modules.homework.select_question', AsyncMock()) as select, \
                patch('modules.homework.read_question', AsyncMock(side_effect=questions)) as read, \
                patch('modules.homework.solve_question', AsyncMock(return_value=[0])), \
                patch('modules.homework.fill_question', AsyncMock()) as fill:
            await complete_homework(page, {}, Mock(), Mock(), AsyncMock())
        self.assertEqual(read.await_count, 5)
        self.assertEqual(fill.await_count, 5)
        # 读题用答题卡，填写时仅回到首题，后续由“下一题”推进。
        self.assertEqual(select.await_count, 6)
        names = [call.kwargs['name'] for call in page.get_by_role.call_args_list if call.args[0] == 'button']
        self.assertEqual(names, ['下一题'] * 4 + ['保存', '提交作业'])
        self.assertEqual(page.get_by_text.call_args_list, [
            call('保存成功！', exact=True), call('返回作业考试', exact=True),
        ])
        box.get_by_role.assert_called_once_with('button', name='确定', exact=True)

    async def test_filling_different_question_stops_before_any_click(self):
        page = Mock()
        expected = {'id': '2', 'type': '判断题', 'options': [{'text': '对'}, {'text': '错'}]}
        current = dict(expected, id='3')
        with patch('modules.homework.read_question', AsyncMock(return_value=current)):
            with self.assertRaisesRegex(RuntimeError, '题号 2.*题号 3'):
                await fill_question(page, expected, [0])
        page.locator.return_value.locator.return_value.count.assert_not_called()

    async def test_incomplete_saved_homework_never_reaches_submit(self):
        page = Mock()
        page.locator.return_value.count = AsyncMock(return_value=1)
        button = Mock(click=AsyncMock())
        heading = Mock(wait_for=AsyncMock(side_effect=PlaywrightTimeout('67%')))
        page.get_by_role.side_effect = lambda role, **kw: heading if role == 'heading' else button
        page.get_by_text.return_value.wait_for = AsyncMock()
        question = {'id': '1', 'type': '判断题', 'options': [{'text': '对'}], 'image': None}
        with patch('modules.homework.wait_activity_content', AsyncMock()), \
                patch('modules.homework.wait_verification', AsyncMock()), \
                patch('modules.homework.select_question', AsyncMock()), \
                patch('modules.homework.read_question', AsyncMock(return_value=question)), \
                patch('modules.homework.solve_question', AsyncMock(return_value=[0])), \
                patch('modules.homework.fill_question', AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, '完成率未达到'):
                await complete_homework(page, {}, Mock(), Mock(), AsyncMock())
        button.click.assert_awaited_once()
        self.assertNotIn('提交作业', [c.kwargs['name'] for c in page.get_by_role.call_args_list])

    async def test_pending_next_navigation_cannot_skip_even_numbered_questions(self):
        page = Mock()
        visible, pending_next, filled = 1, False, set()

        async def select(_, number):
            nonlocal visible, pending_next
            visible = number
            # 旧流程再次点击答题卡后，未完成的“下一题”回调又推进一次。
            if pending_next:
                visible += 1
                pending_next = False

        async def read(_):
            return {'id': str(visible), 'type': '判断题', 'options': [{'text': '对'}], 'image': None}

        async def fill(_, question, answer):
            self.assertEqual(str(visible), question['id'], '发生了错位填写')
            filled.add(visible)

        async def next_question():
            nonlocal pending_next
            pending_next = True

        def number_filter(has_text):
            async def wait_for(**kwargs):
                nonlocal visible, pending_next
                if pending_next:
                    visible += 1
                    pending_next = False
                self.assertIsNotNone(has_text.fullmatch(str(visible)))
            return SimpleNamespace(wait_for=wait_for)

        numbers = Mock(count=AsyncMock(return_value=3))
        box = Mock(wait_for=AsyncMock())
        box.get_by_role.return_value.click = AsyncMock()
        page.locator.side_effect = lambda selector: (
            numbers if selector == '.answerCard_list1 li' else
            SimpleNamespace(filter=number_filter) if selector.endswith('.subject_num') else
            SimpleNamespace(filter=lambda **kw: box)
        )
        next_button = Mock(click=next_question)
        page.get_by_role.side_effect = lambda role, **kw: (
            next_button if kw.get('name') == '下一题'
            else Mock(click=AsyncMock(), wait_for=AsyncMock())
        )
        page.get_by_text.return_value.wait_for = AsyncMock()
        with patch('modules.homework.wait_activity_content', AsyncMock()), \
                patch('modules.homework.wait_verification', AsyncMock()), \
                patch('modules.homework.select_question', side_effect=select), \
                patch('modules.homework.read_question', side_effect=read), \
                patch('modules.homework.fill_question', side_effect=fill), \
                patch('modules.homework.solve_question', AsyncMock(return_value=[0])):
            await complete_homework(page, {}, Mock(), Mock(), AsyncMock())
        self.assertEqual(filled, {1, 2, 3})

    async def test_unsubmitted_card_cannot_confirm_submission(self):
        page = Mock()
        page.reload = AsyncMock()
        page.get_by_text.return_value.click = AsyncMock()
        page.locator.return_value.wait_for = AsyncMock()
        card = page.locator.return_value.filter.return_value.filter.return_value
        card.wait_for = AsyncMock(side_effect=PlaywrightTimeout('no grade'))
        self.assertFalse(await submitted(page, '第五章单元测试'))
        page.locator.return_value.filter.return_value.filter.assert_called_once_with(has_text='作业成绩')

    async def test_activity_failure_is_reported_to_main(self):
        video, home, activity = Mock(), Mock(), Mock()
        video.context.new_page = AsyncMock(side_effect=[home, activity])
        video.locator.return_value.all_text_contents = AsyncMock(return_value=[])
        home.close, activity.close = AsyncMock(), AsyncMock()
        course = {'account': 'hash', 'report': 'report', 'title': '课程'}
        config = SimpleNamespace(qa_enabled=True, homework_enabled=False, config_path='config.ini', video_enabled=False)
        with patch('modules.course_activities.find_course', AsyncMock(return_value=(Mock(), course))), \
                patch('modules.course_activities.run_qa_if_needed', AsyncMock(side_effect=RuntimeError('failed'))), \
                patch('modules.course_activities.ActivityState'), patch('modules.course_activities.AIClient'):
            self.assertFalse(await run_course_activities(video, config, Mock()))
        home.close.assert_awaited_once()
        activity.close.assert_awaited_once()

    async def test_selection_waits_for_style_updates_without_hidden_input_checked(self):
        for kind in ('单选题', '判断题', '多选题'):
            with self.subTest(kind=kind):
                selected = [True, False, False]
                pending = set()
                rows = []
                for index in range(3):
                    row = Mock()

                    async def count(i=index):
                        return int(selected[i])

                    async def wait_for(state, timeout, i=index):
                        # 模拟 click 返回后样式才更新；旧代码立即 count 会误判。
                        if i in pending:
                            if kind == '多选题':
                                selected[i] = not selected[i]
                            else:
                                selected[:] = [n == i for n in range(3)]
                            pending.remove(i)
                        self.assertEqual(selected[i], state == 'attached')

                    async def click(i=index):
                        pending.add(i)

                    row.locator.side_effect = lambda selector, c=count, k=click, w=wait_for: (
                        SimpleNamespace(count=c, wait_for=w) if selector == '.node_detail.onChecked'
                        else SimpleNamespace(click=k) if selector == '.node_detail'
                        else self.fail('不得依赖真站未更新的 input.checked')
                    )
                    rows.append(row)
                page = Mock()
                page.locator.return_value.locator.return_value = SimpleNamespace(
                    count=AsyncMock(return_value=3), nth=lambda i: rows[i],
                )
                question = {'id': '2', 'type': kind, 'options': [{'text': '甲'}, {'text': '乙'}, {'text': '丙'}]}
                choices = [1, 2] if kind == '多选题' else [1]
                with patch('modules.homework.read_question', AsyncMock(return_value=question)):
                    await fill_question(page, question, choices)
                self.assertEqual(selected, [i in choices for i in range(3)])

    async def test_selection_timeout_reports_question_and_never_reclicks(self):
        page = Mock()
        rows = page.locator.return_value.locator.return_value
        rows.count = AsyncMock(return_value=2)
        rows.all_text_contents = AsyncMock(return_value=['甲'])
        row = rows.nth.return_value
        marked = Mock(count=AsyncMock(return_value=0), wait_for=AsyncMock(side_effect=PlaywrightTimeout('late')))
        target = Mock(click=AsyncMock())
        row.locator.side_effect = lambda selector: marked if selector == '.node_detail.onChecked' else target
        question = {'id': '4', 'type': '单选题', 'options': [{'text': '甲'}, {'text': '乙'}]}
        with patch('modules.homework.read_question', AsyncMock(return_value=question)):
            with self.assertRaisesRegex(RuntimeError, '第 4 题.*期望.*乙.*实际.*甲'):
                await fill_question(page, question, [1])
        target.click.assert_awaited_once()

    async def test_reading_second_question_excludes_hidden_questions_and_normalizes_number(self):
        current, page = Mock(), Mock()
        stem = Mock()
        stem.wait_for = AsyncMock()
        stem.inner_text = AsyncMock(return_value='可见的第二题题干')
        rows = []
        for text in ('情绪', '元认知', '应激', '压力源'):
            row = Mock()
            row.locator.return_value.inner_text = AsyncMock(return_value=text)
            row.locator.return_value.get_attribute = AsyncMock(return_value=text)
            rows.append(row)
        fields = {
            '.subject_num': SimpleNamespace(inner_text=AsyncMock(return_value='2.')),
            '.subject_type': SimpleNamespace(inner_text=AsyncMock(return_value='【单选题】 (2分)')),
            '.subject_describe': stem,
            '.subject_node .nodeLab': SimpleNamespace(all=AsyncMock(return_value=rows)),
        }
        current.locator.side_effect = fields.__getitem__
        page.locator.side_effect = lambda selector: current if selector == CURRENT_QUESTION else self.fail('不得读取全页隐藏题目')
        question = await read_question(page, capture_image=False)
        self.assertEqual(question['id'], '2')
        self.assertEqual([option['text'] for option in question['options']], ['情绪', '元认知', '应激', '压力源'])

    async def test_select_question_waits_on_visible_number_with_optional_dot(self):
        page = Mock()
        number = page.locator.return_value.filter.return_value
        number.click = AsyncMock()
        number.wait_for = AsyncMock()
        await select_question(page, 2)
        selectors = [call.args[0] for call in page.locator.call_args_list]
        self.assertIn('.examPaper_subject:visible .subject_num', selectors)
        pattern = page.locator.return_value.filter.call_args.kwargs['has_text']
        self.assertTrue(pattern.fullmatch('2.'))
        self.assertTrue(pattern.fullmatch('2'))
        self.assertFalse(pattern.fullmatch('12'))


class MappingTests(unittest.TestCase):
    def test_exams_are_excluded_even_when_open_and_actionable(self):
        self.assertTrue(is_homework_card('作业', '第二章单元测试 开始答题', True))
        for kind in ('在线考试', '考试', '', '作业考试'):
            self.assertFalse(is_homework_card(kind, '已开放 开始答题', True))
        self.assertFalse(is_homework_card('作业', '尚未开始', True))

    def test_option_content_mapping_and_validation(self):
        question = {'id': '2', 'type': '多选题', 'options': [{'text': '乙'}, {'text': '甲'}, {'text': '丙'}]}
        self.assertEqual(map_answer(question, {'question_id': '2', 'choices': ['甲', '乙']}), [1, 0])
        for answer in ({'question_id': '9', 'choices': ['甲']}, {'question_id': '2', 'choices': ['A']},
                       {'question_id': '2', 'choices': ['甲', '甲']}):
            with self.assertRaises(RuntimeError):
                map_answer(question, answer)

    def test_one_ini_without_mirrors_json_and_raw_percent_key(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'config.ini'
            original = Path('config.ini.example').read_text(encoding='utf-8-sig')
            path.write_text(original.replace('api_key =', 'api_key = test%key'), encoding='utf-8')
            config = Config(path)
            self.assertEqual(config.ai_api_key, 'test%key')
            self.assertEqual(config.ai_model, 'deepseek-flash')
            self.assertFalse(config.qa_enabled)
            self.assertFalse(config.homework_enabled)
            self.assertEqual(len(config.mirrors), 4)
            self.assertFalse((Path(root) / 'data').exists())
            path.write_text(original.split('[ai]')[0], encoding='utf-8')
            legacy = Config(path)
            self.assertEqual(legacy.mirrors, DEFAULT_MIRRORS)
            self.assertFalse(legacy.qa_enabled)


class AIClientTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return SimpleNamespace(ai_api_key='test%key', ai_base_url='https://example.test/v1',
                               ai_model='deepseek-flash', ai_timeout=1, ai_max_retries=1)

    async def test_rate_limit_retries_and_json_payload(self):
        requests = []

        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(429)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"text":"ok"}'}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('modules.ai_client.httpx.AsyncClient', return_value=client), \
                patch('modules.ai_client.asyncio.sleep', AsyncMock()):
            result = await AIClient(self.config()).generate('返回 JSON', {'course': '课程'})
        self.assertEqual(result, {'text': 'ok'})
        self.assertEqual(len(requests), 2)
        self.assertEqual(str(requests[0].url), 'https://example.test/v1/chat/completions')
        payload = json.loads(requests[0].content)
        self.assertNotIn('test%key', str(payload))
        self.assertIsInstance(payload['messages'][1]['content'], str)

    async def test_auth_failure_is_not_retried_or_echoed(self):
        handler = Mock(return_value=httpx.Response(401, text='test%key'))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('modules.ai_client.httpx.AsyncClient', return_value=client):
            with self.assertRaisesRegex(AIError, '鉴权失败') as error:
                await AIClient(self.config()).generate('JSON', {})
        self.assertNotIn('test%key', str(error.exception))
        self.assertEqual(handler.call_count, 1)

    async def test_network_timeout_stops_at_retry_limit(self):
        handler = Mock(side_effect=httpx.ReadTimeout('timeout'))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('modules.ai_client.httpx.AsyncClient', return_value=client), \
                patch('modules.ai_client.asyncio.sleep', AsyncMock()):
            with self.assertRaisesRegex(AIError, '超时'):
                await AIClient(self.config()).generate('JSON', {})
        self.assertEqual(handler.call_count, 2)
