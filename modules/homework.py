"""只处理未提交列表里明确标记为“作业”的项目。"""
import base64
import json
import re

from playwright.async_api import TimeoutError

from modules.activity_state import fingerprint
from modules.course_activities import wait_activity_content, wait_verification


SUPPORTED_TYPES = {'单选题', '多选题', '判断题'}
CURRENT_QUESTION = '.examPaper_subject:visible'


def is_homework_card(type_label, text, can_start):
    return type_label.strip() == '作业' and '考试' not in type_label and can_start and not any(
        word in text for word in ('已截止', '尚未开始', '未开放', '已提交')
    )


def has_encoded_text(text):
    return any(0xE000 <= ord(char) <= 0xF8FF or char == '\ufffd' for char in text)


def map_answer(question, result):
    if result.get('question_id') != question['id']:
        raise RuntimeError(f'答案题号 {result.get("question_id")} 与当前题号 {question["id"]} 不一致')
    values = result.get('choices')
    if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
        raise RuntimeError('AI 答案必须是非空选项内容列表')
    options = question['options']
    selected = []
    for value in values:
        matches = [index for index, option in enumerate(options) if option['text'].strip() == value.strip()]
        if len(matches) != 1:
            raise RuntimeError('AI 答案无法唯一映射到实际选项内容')
        selected.append(matches[0])
    if len(set(selected)) != len(selected) or (question['type'] != '多选题' and len(selected) != 1):
        raise RuntimeError('AI 返回了重复选项或单选题的多个答案')
    return selected


async def select_question(page, number):
    await page.locator('.answerCard_list1 li').filter(has_text=re.compile(rf'^{number}$')).click()
    await page.locator(f'{CURRENT_QUESTION} .subject_num').filter(
        has_text=re.compile(rf'^\s*{number}\.?\s*$'),
    ).wait_for(state='visible')


async def read_question(page, capture_image=True):
    current = page.locator(CURRENT_QUESTION)
    number = re.search(r'\d+', await current.locator('.subject_num').inner_text()).group()
    type_text = await current.locator('.subject_type').inner_text()
    match = re.search(r'【(.+?)】', type_text)
    kind = match.group(1) if match else type_text.strip()
    if kind not in SUPPORTED_TYPES:
        raise RuntimeError(f'暂不支持题型：{kind}，本作业未提交')
    stem = current.locator('.subject_describe')
    await stem.wait_for(state='visible')
    text = (await stem.inner_text()).strip()
    options = []
    for row in await current.locator('.subject_node .nodeLab').all():
        options.append({'text': (await row.locator('.node_detail').inner_text()).strip(),
                        'value': await row.locator('input').get_attribute('value')})
    if len(options) < 2 or any(not option['text'] for option in options):
        raise RuntimeError('选项内容缺失，停止本作业')
    if any(has_encoded_text(option['text']) for option in options):
        raise RuntimeError('选项含尚未验证的字体编码，无法按内容映射，停止本作业')
    image = None
    # 普通文本优先。当前页面的题干在可读 DOM 中为空；只截题目，不截账号或整页。
    if capture_image and (not text or has_encoded_text(text) or await stem.locator('img, canvas, svg').count()):
        image = 'data:image/png;base64,' + base64.b64encode(
            await stem.screenshot()
        ).decode('ascii')
    return {'id': number, 'type': kind, 'text': text, 'options': options, 'image': image}


async def solve_question(question, course, state, ai):
    # 以选项内容缓存，不缓存 A/B/C：平台明确提示选项顺序随机。
    cache_key = fingerprint(json.dumps({
        'type': question['type'], 'text': question['text'],
        'options': sorted(o['text'] for o in question['options']),
        'image': fingerprint(question['image']) if question['image'] else None,
    }, ensure_ascii=False, sort_keys=True))
    cached = state.records['answers'].get(cache_key)
    if cached:
        result = {'question_id': question['id'], 'choices': cached}
    else:
        result = await ai.generate(
            '解答课程作业中的选择题。题干可能在图片中，请读清题意；'
            '返回 {"question_id":"原题号", "choices":["所选选项的完整内容"]}。'
            '多选题列出全部正确选项，不返回选项字母。',
            {'course': course['title'], 'chapters': course['chapters'],
             'question_id': question['id'], 'type': question['type'], 'question': question['text'],
             'options': [o['text'] for o in question['options']]}, question['image'],
        )
    selected = map_answer(question, result)
    state.records['answers'][cache_key] = [question['options'][index]['text'] for index in selected]
    state.save()
    return selected


async def fill_question(page, question, selected):
    rows = page.locator(CURRENT_QUESTION).locator('.subject_node .nodeLab')
    # 回到题目后重新按内容取索引，以免顺序发生变化。
    current = await read_question(page, capture_image=False)
    selected_text = [question['options'][i]['text'] for i in selected]
    indices = map_answer(current, {'question_id': question['id'], 'choices': selected_text})
    try:
        for index in range(await rows.count()):
            row = rows.nth(index)
            # 真站单选/判断的隐藏 input.checked 不更新，使用实际选中样式。
            marked = row.locator('.node_detail.onChecked')
            checked = await marked.count() > 0
            wanted = index in indices
            if wanted != checked and (current['type'] == '多选题' or wanted):
                await row.locator('.node_detail').click()
                # 页面异步更新样式，不能在 click 返回后立即 count 判错。
                await marked.wait_for(state='attached' if wanted else 'detached', timeout=5000)
        for index in range(await rows.count()):
            await rows.nth(index).locator('.node_detail.onChecked').wait_for(
                state='attached' if index in indices else 'detached', timeout=5000,
            )
    except TimeoutError as error:
        actual = await page.locator(CURRENT_QUESTION).locator('.node_detail.onChecked').all_text_contents()
        raise RuntimeError(
            f'第 {current["id"]} 题（{current["type"]}）选项未正确勾选：'
            f'期望 {selected_text}，实际 {[text.strip() for text in actual]}，停止提交'
        ) from error


async def complete_homework(page, course, logger, state, ai):
    await wait_activity_content(page, '.answerCard_list1 li', logger, '作业题号列表')
    await wait_activity_content(page, f'{CURRENT_QUESTION} .subject_type', logger, '作业首题')
    count = await page.locator('.answerCard_list1 li').count()
    if not count:
        raise RuntimeError('未读取到作业题号列表')
    logger.info(f'作业题号加载完成，共 {count} 题。')
    questions = []
    # 全部题型先读完；不支持的题型在填写和提交前报出。
    for number in range(1, count + 1):
        await wait_verification(page, logger)
        await select_question(page, number)
        question = await read_question(page)
        questions.append(question)
        source = '题干截图' if question['image'] else '题干文本'
        logger.info(f'已读取第 {number}/{count} 题：{question["type"]}，{len(question["options"])} 个选项，使用{source}。')
    logger.info('开始解答作业，已有缓存的题目复用答案。')
    answers = [await solve_question(question, course, state, ai) for question in questions]
    for number, (question, selected) in enumerate(zip(questions, answers), 1):
        await wait_verification(page, logger)
        logger.info(f'正在定位并填写第 {number}/{count} 题。')
        if number == 1:
            await select_question(page, number)
        else:
            # “下一题”已保存并切题，不再次点击答题卡，等待真正显示目标题号。
            await page.locator(f'{CURRENT_QUESTION} .subject_num').filter(
                has_text=re.compile(rf'^\s*{number}\.?\s*$'),
            ).wait_for(state='visible')
        await fill_question(page, question, selected)
        logger.info(f'第 {number}/{count} 题已填写并确认选中状态。')
        if number < count:
            logger.info(f'正在保存第 {number}/{count} 题并进入下一题。')
            await page.get_by_role('button', name='下一题', exact=True).click()
    # 最后一题底部按钮变为“保存”；单选/判断的状态由 onChecked 标记。
    logger.info('正在保存最后一题。')
    await page.get_by_role('button', name='保存', exact=True).click()
    await page.get_by_text('保存成功！', exact=True).wait_for(state='visible')
    try:
        await page.get_by_role('heading', name=re.compile(r'^完成率\s*100\s*%$')).wait_for(
            state='visible', timeout=5000,
        )
    except TimeoutError as error:
        raise RuntimeError('作业已保存，但完成率未达到 100%，可能仍有漏答，停止提交') from error
    logger.info(f'全部 {count} 题已保存，页面完成率为 100%。')
    await wait_verification(page, logger)
    logger.info('保存完成，正在请求提交作业。')
    await page.get_by_role('button', name='提交作业', exact=True).click()
    box = page.locator('.el-message-box').filter(has_text='是否确认提交')
    await box.wait_for(state='visible')
    await box.get_by_role('button', name='确定', exact=True).click()
    # 等待站点完成提交，不能点完确认后立刻关闭仍在提交的页面。
    await page.get_by_text('返回作业考试', exact=True).wait_for(state='visible')


async def submitted(list_page, title):
    await list_page.reload()
    await list_page.get_by_text('已提交', exact=True).click()
    await list_page.locator('#examStateTabYsj.cur').wait_for()
    # 切页期间旧的未提交卡片可能仍存在，只匹配带“作业成绩”的结果卡片。
    card = list_page.locator('.examItemWrap').filter(
        has=list_page.locator('.course_ewname').filter(has_text=re.compile(r'^\s*' + re.escape(title) + r'\s*$')),
    ).filter(has_text='作业成绩')
    try:
        await card.wait_for(state='visible', timeout=15000)
    except TimeoutError:
        return False
    return True


async def run_homework(page, course, logger, state, ai):
    await wait_verification(page, logger)
    await page.locator('#examStateTabWsj.cur').wait_for()
    await page.locator('.examBox').wait_for(state='visible')
    await page.locator('.examItemWrap').first.wait_for(state='visible')
    titles = []
    for card in await page.locator('.examItemWrap').all():
        badge = card.locator('.percentage_number')
        label = await badge.inner_text() if await badge.count() else ''
        button = card.get_by_text('开始答题', exact=True)
        if is_homework_card(label, await card.inner_text(), await button.count() > 0 and await button.is_visible()):
            titles.append((await card.locator('.course_ewname').inner_text()).strip())
    for title in titles:
        if not await page.locator('#examStateTabWsj.cur').count():
            await page.get_by_text('未提交', exact=True).click()
            await page.locator('#examStateTabWsj.cur').wait_for(state='visible')
        card = page.locator('.examItemWrap').filter(has=page.locator('.course_ewname').filter(has_text=re.compile(r'^\s*' + re.escape(title) + r'\s*$')))
        async with page.expect_popup() as popup:
            await card.get_by_text('开始答题', exact=True).click()
        work = await popup.value
        work.set_default_timeout(15000)
        try:
            try:
                await complete_homework(work, course, logger, state, ai)
            except TimeoutError as error:
                # 提交按钮可能已经生效；先查列表，绝不直接再次点提交。
                if not await submitted(page, title):
                    raise RuntimeError(f'作业“{title}”操作超时，未确认提交，不自动重试。原始错误：{error}') from error
            if not await submitted(page, title):
                raise RuntimeError(f'作业“{title}”未在已提交列表确认，不自动重试')
            logger.info(f'作业“{title}”已在已提交列表确认。')
        finally:
            await work.close()
