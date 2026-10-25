"""问答 UI：有限数量发布，用个人列表确认，超时不重复点击。"""
import asyncio
import re
from datetime import date

from playwright.async_api import TimeoutError

from modules.activity_state import fingerprint
from modules.course_activities import wait_activity_content, wait_verification


def normalize(text):
    return re.sub(r'[\W_]+', '', text).lower()


async def open_qa_home(page, course):
    if '/web/home/' in page.url:
        return
    back = page.locator('.courseArrangement-box').get_by_text('课程问答', exact=True)
    if await back.count():
        await back.click()
    else:
        await page.goto(course['qa'], wait_until='domcontentloaded')


async def wait_questions(page, logger):
    selector = '.question-item, .tip-container:has-text("没有更多")'
    try:
        await wait_activity_content(page, selector, logger, '问题列表')
    except RuntimeError:
        logger.warn('问题列表未加载，重新加载此只读页面一次。')
        await page.reload(wait_until='domcontentloaded')
        await wait_activity_content(page, selector, logger, '问题列表')


async def personal_records(page, course, kind, logger=None):
    name = '我的提问' if kind == 'question' else '我的回答'
    if '/web/myJoin/' in page.url:
        await page.locator('.tab-item').filter(has_text=name).click()
    else:
        await open_qa_home(page, course)
        await wait_activity_content(page, '.qa_jion_name', logger, '个人问答入口')
        await page.get_by_text(name, exact=True).click()
    await wait_activity_content(page, f'.tab-item.active:has-text("{name}")', logger, name)
    # 等到列表明确结束，不能把加载中当成零条；滚动加载其余个人记录。
    for _ in range(100):
        tip = page.locator('.tip-container')
        await tip.wait_for(state='visible')
        if '没有更多' in await tip.inner_text():
            break
        await tip.scroll_into_view_if_needed()
        await asyncio.sleep(0.3)
    else:
        raise RuntimeError('个人发布列表未完整加载，停止问答以免重复')
    return await page.locator('.question-item').all()


async def personal_questions(page, course, logger=None):
    return [await item.locator('.question-content').inner_text()
            for item in await personal_records(page, course, 'question', logger)]


async def confirm_post(page, course, record, logger=None):
    """重新读取已发表内容。找不到时保持待核实记录，留待下次运行查询。"""
    if record['kind'] == 'question':
        texts = await personal_questions(page, course, logger)
    else:
        texts = [await item.locator('.question-content').inner_text()
                 for item in await personal_records(page, course, 'answer', logger)
                 if normalize(await item.locator('.qustion-main-content').inner_text()) == normalize(record['question'])]
    target = normalize(record['text'])
    return any(target and target == normalize(text) for text in texts)


async def publish(page, course, kind, key, text, state, logger, question=None):
    await wait_verification(page, logger)
    if kind == 'question':
        await page.locator('.ask-btn').click()
        dialog = page.get_by_role('dialog', name='提问', exact=True)
    else:
        await page.get_by_text('我来回答', exact=False).click()
        dialog = page.get_by_role('dialog', name='我来回答', exact=True)
    editor = dialog.locator('textarea')
    await editor.fill(text)
    if await editor.input_value() != text:
        raise RuntimeError('问答编辑器未保留完整正文，停止发布')
    record = state.begin(kind, key, text, page.url, question)
    try:
        await dialog.get_by_text('立即发布', exact=True).click()
        await wait_verification(page, logger)
        await dialog.wait_for(state='hidden')
    except TimeoutError:
        logger.warn('发布超时，先读取已发布结果，不重复发布。')
    if await confirm_post(page, course, record, logger):
        state.confirm(record)
        logger.info('问答发布已在个人列表确认。')
    else:
        raise RuntimeError('发布结果待核实，保留本地记录，停止本课程问答以免重复发布')


def generated_text(result, question=False):
    text = result.get('text')
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 1000:
        raise RuntimeError('AI 问答内容为空或超过 1000 字')
    text = text.strip()
    if question and not text.endswith(('？', '?')):
        raise RuntimeError('AI 提问必须以问号结尾')
    return text


async def run_qa(page, course, config, logger, state, ai):
    for record in state.records['posts']:
        if not record['confirmed']:
            if await confirm_post(page, course, record, logger):
                state.confirm(record)
            else:
                raise RuntimeError('上次发布仍未确认，停止本课程问答；请检查个人发布列表')

    # 每次上限同时作为当天上限；次日分数更新后重新检查。
    answers_left = state.remaining('answer', config.qa_answers)
    questions_left = state.remaining('question', config.qa_questions)
    if not (answers_left or questions_left):
        logger.info('今天本课程的问答数量已达上限，次日查分后再执行。')
        return
    own_questions = await personal_records(page, course, 'question', logger)
    previous = [await item.locator('.question-content').inner_text() for item in own_questions]
    today_questions = sum([date.today().isoformat() in await item.locator('.qb-time').inner_text()
                           for item in own_questions])
    questions_left = min(questions_left, max(0, config.qa_questions - today_questions))
    previous += [p['text'] for p in state.records['posts'] if p['kind'] == 'question']
    own_answers = await personal_records(page, course, 'answer', logger)
    today_answers = sum([date.today().isoformat() in await item.locator('.qb-time').inner_text()
                         for item in own_answers])
    answers_left = min(answers_left, max(0, config.qa_answers - today_answers))
    logger.info(f'今日已发布：提问 {today_questions}/{config.qa_questions}，回答 {today_answers}/{config.qa_answers}。')
    own_titles = {normalize(await item.locator('.qustion-main-content').inner_text()) for item in own_answers}
    if not (answers_left or questions_left):
        logger.info('个人发布列表显示今日问答已达上限，次日查分后再执行。')
        return
    candidates = []
    if answers_left:
        await open_qa_home(page, course)
        await page.get_by_text('最新', exact=True).click()
        try:
            await wait_questions(page, logger)
        except RuntimeError as error:
            logger.warn(f'本次回答跳过：{error}')
        else:
            for item in await page.locator('.question-item').all():
                title = await item.locator('.question-content').inner_text()
                count = await item.locator('.qb-answers').inner_text()
                candidates.append((int(re.search(r'\d+', count).group()), title))
            logger.info(f'问题列表已加载，共 {len(candidates)} 条，开始筛选尚未回答的问题。')
    # 先处理回答少的问题；不为达到数量反复回答同一问题。
    for _, title in sorted(candidates, key=lambda row: row[0]):
        if not answers_left:
            break
        if normalize(title) in own_titles or any(normalize(title) == normalize(old) for old in previous):
            continue
        item = page.locator('.question-item').filter(has=page.locator('.question-content').filter(has_text=title)).first
        async with page.expect_popup() as popup:
            await item.locator('.question-content').click()
        detail = await popup.value
        detail.set_default_timeout(15000)
        try:
            await detail.get_by_text('我来回答', exact=False).wait_for()
            # ID 来自站点正常打开的 URL，不构造详情路由或签名。
            key = detail.url.split('?')[0]
            if state.seen('answer', key):
                continue
            result = await ai.generate(
                '根据课程内容回答给定问题，具体解释概念或方法，不虚构个人经历。'
                '如果与课程无关返回 {"skip":true}，否则返回 {"text":"回答"}。',
                {'course': course['title'], 'chapters': course['chapters'], 'question': title},
            )
            if result.get('skip'):
                continue
            await publish(detail, course, 'answer', key, generated_text(result), state, logger, title)
            own_titles.add(normalize(title))
            answers_left -= 1
        finally:
            await detail.close()

    if questions_left:
        for _ in range(questions_left):
            result = await ai.generate(
                '根据课程及章节提出一个有实质内容、适合讨论的问题，不虚构个人经历，'
                '避免与已有问题重复。返回 {"text":"问题？"}。',
                {'course': course['title'], 'chapters': course['chapters'], 'previous_questions': previous},
            )
            text = generated_text(result, question=True)
            key = fingerprint(normalize(text))
            if any(normalize(text) == normalize(old) for old in previous):
                logger.info('AI 生成了重复问题，跳过。')
                continue
            await open_qa_home(page, course)
            await publish(page, course, 'question', key, text, state, logger)
            previous.append(text)
