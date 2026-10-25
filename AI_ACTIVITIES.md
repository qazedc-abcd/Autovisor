# AI 与作业实现说明

用户使用方法见 README。配置统一为 config.ini，没有额外必须填写的 JSON 或环境变量。

- [video] enabled：视频功能；旧配置缺失时默认开启。
- [qa] enabled：互动；提问和回答数量同时作为每课当天上限。
- [homework] enabled：仅未提交的作业，正式考试始终排除。
- [ai]：base_url、api_key、model、timeout、max_retries。密钥按原文读取，支持百分号。
- [mirrors]：下载镜像，兼容旧镜像配置迁移。

互动先读取成绩分析的实际互动得分，满分大于零且分数有效才判定。满分不调用互动 AI、不发布。初始 0/0 不作为发布依据。当天记录结合个人列表去重。

作业使用课程首页正常入口生成的 URL，不猜测接口或签名。仅处理明确标记为“作业”的项目。支持单选、多选、判断。

题干优先读取 DOM；空文本、私用区字符或图片题干使用小截图，不发送账号或认证信息。尚未实现 TTF 字体解码。模型需要支持实际输入方式。

答案按完整选项内容映射，并检查原题号。填写阶段只用“下一题”推进，避免重复点击答题卡。选中状态使用 .node_detail.onChecked，隐藏 input.checked 不可靠。保存成功且完成率 100% 才最终提交，返回结果后核对已提交成绩卡片。

发布或提交超时先核实结果，不盲目重试。可见安全验证由用户处理。AI 正确率、网站统计和审核结果不由本程序保证。

## UI 契约

实现操作 UI，没有把推测的 HTTP 接口作为已确认数据 API。

|用途|页面元素|
|---|---|
|互动成绩|#LRCourseForum_pag、#learnedInteractiveScore、#learnedInteractiveTotalScore|
|问答与个人记录|.question-item、.question-content、.qb-time、.qustion-main-content|
|发布|.ask-btn、我来回答、dialog、textarea、立即发布|
|作业类型|.examItemWrap、.percentage_number 的“作业”、.course_ewname|
|当前题目|.examPaper_subject:visible、.subject_num、.subject_type、.subject_describe|
|选项与保存|.nodeLab、.node_detail、.onChecked、下一题、保存|
|提交|提交作业、.el-message-box、确定、返回作业考试、带“作业成绩”的卡片|

页面改版后可能需要调整。打包启动检查不代表所有课程都已完成真实提交验证。
