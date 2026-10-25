"""课程级编排：独立于视频进度，复用已经登录的 BrowserContext。"""
import asyncio
import math
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import TimeoutError

from modules.activity_state import ActivityState, fingerprint
from modules.ai_client import AIClient
from modules.login import is_login_page, wait_for_login_complete
from modules.tasks import has_visible_verification, wait_until_verification_hidden


HOME_URL = 'https://onlineweb.zhihuishu.com/onlinestuh5'


def same_course(left, right):
    """比较课程标识，不把报告页的 recruitId 当成作业 URL 的加密参数。"""
    a, b = parse_qs(urlparse(left).query), parse_qs(urlparse(right).query)
    for key in ('recruitAndCourseId', 'courseId'):
        if a.get(key) and b.get(key):
            return a[key] == b[key] and (key != 'courseId' or a.get('recruitId') == b.get('recruitId'))
    return left.rstrip('/') == right.rstrip('/')


def parse_score(earned, maximum):
    try:
        earned, maximum = float(earned), float(maximum)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(earned) or not math.isfinite(maximum) or earned < 0 or maximum <= 0:
        return None
    return earned, maximum


async def wait_verification(page, logger):
    if await has_visible_verification(page):
        logger.warn('请在浏览器完成可见的安全验证，程序等待后继续。')
        await page.bring_to_front()
        await wait_until_verification_hidden(page)


async def wait_activity_content(page, selector, logger=None, label='活动页面', timeout=30000):
    """等待实际业务节点；子站登录和可见验证完成后重新计算加载时间。"""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if is_login_page(page.url):
            if logger:
                logger.warn(f'{label}跳转到了登录页，请在该页面完成登录后继续。')
            await page.bring_to_front()
            await wait_for_login_complete(page, timeout=300000)
            deadline = time.monotonic() + timeout / 1000
        if await has_visible_verification(page):
            if logger:
                logger.warn(f'{label}需要安全验证，请在浏览器手动完成。')
            await page.bring_to_front()
            await wait_until_verification_hidden(page)
            deadline = time.monotonic() + timeout / 1000
        try:
            await page.locator(selector).first.wait_for(
                state='visible', timeout=min(1000, max(1, int((deadline - time.monotonic()) * 1000))),
            )
            return
        except TimeoutError:
            continue
    raise RuntimeError(f'{label}业务数据未加载完成；页面可能仍在加载或子站请求失败')


async def read_interaction_score(page, logger, timeout=30):
    await page.locator('#LRCourseForum_pag').click()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await wait_verification(page, logger)
        values = await page.locator('#learnedInteractiveScore, #learnedInteractiveTotalScore').all_text_contents()
        if len(values) == 2:
            score = parse_score(*values)
            if score:
                return score
        await asyncio.sleep(0.3)
    raise RuntimeError('我的互动得分未有效加载（缺失或满分为 0），跳过本课程写操作')


async def run_qa_if_needed(page, course, config, logger, state, ai):
    from modules.qa import run_qa

    await page.goto(course['report'])
    earned, maximum = await read_interaction_score(page, logger)
    logger.info(f'我的互动：{earned:g}/{maximum:g}')
    if earned >= maximum:
        logger.info('互动已满分，跳过全部 AI 问答。')
        return True
    if not config.ai_api_key:
        logger.warn('AI 问答已跳过：请在 config.ini 的 [ai] 填写 api_key。')
        return False
    await page.goto(course['qa'])
    await run_qa(page, course, config, logger, state, ai)
    return True


async def find_course(home, report, course_url):
    await home.goto(HOME_URL)
    await home.locator('#sharingClassed dl').first.wait_for(state='visible')
    account = await home.locator('.user-logo_name').inner_text()
    cards = home.locator('#sharingClassed dl')
    for index in range(await cards.count()):
        card = cards.nth(index)
        link = await card.get_by_text('成绩分析', exact=True).locator('xpath=ancestor::a').get_attribute('href')
        report_url = 'https:' + link if link.startswith('//') else link
        await report.goto(report_url)
        learning = await report.get_by_role('link', name='课程学习', exact=True).get_attribute('href')
        if same_course(learning, course_url):
            qa = await card.get_by_text('问答', exact=True).locator('xpath=ancestor::a').get_attribute('href')
            return card, {'title': await card.locator('.courseName').inner_text(),
                          'report': report_url, 'qa': 'https:' + qa if qa.startswith('//') else qa,
                          'account': fingerprint(account)}
    raise RuntimeError('未能把当前课程与学生首页课程卡片匹配，跳过 AI 活动')


async def run_course_activities(video_page, config, logger, course_url=None):
    if not (config.qa_enabled or config.homework_enabled):
        return True
    completed = True
    home = await video_page.context.new_page()
    page = await video_page.context.new_page()
    home.set_default_timeout(15000)
    page.set_default_timeout(15000)
    try:
        card, course = await find_course(home, page, course_url or video_page.url)
        course['chapters'] = await video_page.locator('.child-name, .item-name, #lessonOrder, .chapter-title').all_text_contents()
        scope = course['account'] + ':' + fingerprint(course['report'])
        state = ActivityState(Path(config.config_path).parent / '.autovisor' / 'activities.json', scope)
        ai = AIClient(config)
        if config.qa_enabled:
            try:
                if await run_qa_if_needed(page, course, config, logger, state, ai) is False:
                    completed = False
            except (RuntimeError, TimeoutError) as error:
                completed = False
                logger.warn(f'问答停止：{error}')
        if config.homework_enabled:
            if not config.ai_api_key:
                completed = False
                logger.warn('AI 作业已跳过：请在 config.ini 的 [ai] 填写 api_key。')
            else:
                from modules.homework import run_homework

                # 实测此入口打开新标签页，保留站点生成的加密 recruitId。
                async with home.expect_popup() as popup:
                    await card.get_by_text('作业考试', exact=True).click()
                homework = await popup.value
                homework.set_default_timeout(15000)
                try:
                    await run_homework(homework, course, logger, state, ai)
                finally:
                    await homework.close()
    except (RuntimeError, TimeoutError, OSError, ValueError) as error:
        completed = False
        next_step = '视频功能继续' if config.video_enabled else '继续下一门课程'
        logger.warn(f'课程 AI 活动停止：{error}；{next_step}。')
    finally:
        await home.close()
        await page.close()
    return completed
