# 个人 Agent 任务拆分

来源：`personal-agent-prd.md`  
版本：V0.1 拆分稿  
日期：2026-05-04

## 1. 总体目标

个人 Agent 需要完成的核心事情不是“自动替用户决策”，而是把分散的信息变成一套可确认、可追溯、可沉淀、可发布的工作流。

整体链路：

```text
采集信息
→ 标准化为统一事件
→ 进入收件箱
→ 生成摘要 / TODO / 笔记 / 图谱关系候选
→ 用户审核确认
→ 写入 TODO / 知识库 / 飞书文档 / Obsidian
→ 可公开内容进入脱敏流程
→ 用户确认后同步 GitHub
```

## 2. Agent 角色拆分

## 2.1 Orchestrator Agent

### 定位

负责调度各类 Agent，维护信息处理状态机，保证所有自动化动作都经过正确的入口、状态和审计链路。

### 需要做的事情

- 定义统一任务流：采集、入箱、整理、审核、归档、同步、发布。
- 为每个 SourceEvent 创建后续处理任务。
- 调度 Summarizer、Task Extractor、Knowledge Curator、Graph Builder、Sanitizer 等 Agent。
- 维护 InboxItem、Task、Note、Relation、SyncLog 的状态流转。
- 避免未确认内容直接进入长期知识库、飞书文档或发布目录。
- 记录每一步处理日志和失败原因。

### 输入

- SourceEvent
- 用户配置
- 用户手动触发动作
- 定时任务触发

### 输出

- Agent 执行任务
- 状态变更
- 审计日志
- 错误和重试记录

### MVP 优先级

高。即使 MVP 阶段不做复杂编排，也需要有最小状态机，避免后续逻辑散落在 UI 或具体 Agent 内。

## 2.2 Collector Agent

### 定位

负责从飞书、手动备忘、本地文件和文档中采集信息，并统一转换为 SourceEvent。

### 需要做的事情

- 支持手动备忘输入，并生成 SourceEvent。
- 支持手动导入文本、Markdown 或模拟飞书摘要。
- 读取用户配置的关注飞书群。
- 按配置采集指定时间窗口内的群聊消息。
- 记录来源类型、来源 ID、来源链接、作者、创建时间和采集时间。
- 对采集失败场景保存失败原因，进入重试队列。
- 控制采集范围，V0.1 不做全量群聊扫描。

### 输入

- 手动备忘内容
- 本地文件
- 飞书群聊配置
- 飞书 MCP 或 OpenAPI 返回消息

### 输出

- SourceEvent
- 采集日志
- 失败重试记录

### MVP 优先级

高。MVP 至少要支持手动备忘和模拟飞书摘要；V0.2 再接入真实飞书群聊。

## 2.3 Summarizer Agent

### 定位

负责把群聊、会议记录、长文档或资料整理成结构化摘要。

### 需要做的事情

- 生成群聊摘要，包含核心结论、重要上下文、风险阻塞、关键人物、关键项目和知识点。
- 为摘要保留原始消息来源索引。
- 支持按时间窗口总结，例如昨日、最近一次、指定时间段。
- 对长文档或会议记录生成摘要草稿。
- 将摘要结果写入收件箱，默认状态为待确认。
- 标记摘要置信度和可能需要补充的信息。

### 输入

- SourceEvent 列表
- 群聊时间窗口
- 摘要 Prompt
- 用户关注范围配置

### 输出

- InboxItem，类型为摘要候选
- 摘要来源索引
- 风险和阻塞候选
- 值得沉淀的知识点候选

### MVP 优先级

中。MVP 可先支持手动导入内容的摘要；V0.2 支持真实飞书群聊摘要。

## 2.4 Task Extractor Agent

### 定位

负责从群聊、备忘、文档和摘要中抽取用户需要处理的 TODO。

### 需要做的事情

- 判断一段内容是否包含个人待办。
- 抽取 TODO 标题、描述、来源、项目、负责人、截止时间、优先级和置信度。
- 区分候选 TODO 和已确认 TODO。
- 将 TODO 候选放入收件箱或 TODO 中心的候选态。
- 支持用户编辑后确认。
- 对误识别的 TODO 记录反馈，用于优化 Prompt 或规则。

### 输入

- SourceEvent
- InboxItem
- 摘要内容
- 备忘内容
- 文档摘录

### 输出

- Task 候选
- InboxItem，类型为 TODO 候选
- 抽取置信度
- 来源消息或来源文件索引

### MVP 优先级

高。MVP 的核心价值之一就是从备忘和摘要中生成可确认 TODO。

## 2.5 Review Agent

### 定位

负责把 AI 生成的候选结果组织成用户可确认、可编辑、可忽略的审核工作流。

### 需要做的事情

- 将所有 AI 结果先放入收件箱。
- 支持候选项的确认、编辑后确认、忽略、标记误识别、归档。
- 支持批量确认、批量忽略、批量修改分类。
- 展示每个候选项的来源、分类、状态、置信度和更新时间。
- 高风险动作触发二次确认。
- 记录每次用户操作。

### 输入

- InboxItem
- Task 候选
- Note 候选
- Relation 候选
- Sanitizer 检测结果

### 输出

- 已确认任务
- 已确认笔记
- 已忽略内容
- 用户修改后的分类和元数据
- 审核日志

### MVP 优先级

高。PRD 明确要求人审优先，Review Agent 是保证可信度的关键。

## 2.6 Knowledge Curator Agent

### 定位

负责把确认后的内容整理为 Obsidian 友好的 Markdown 知识笔记。

### 需要做的事情

- 根据内容类型选择知识库分区：工作记录、项目笔记、学习资料、方法论、会议纪要、人物与协作、待整理资料、可公开资料。
- 生成 Markdown 笔记正文。
- 生成 YAML Frontmatter。
- 保留 source、source_url、created_at、updated_at、sensitivity、publishable 等字段。
- 生成 Obsidian 双链，例如 `[[项目名]]`、`[[概念名]]`。
- 生成标签，例如 `#tag`。
- 检查是否与已有笔记重复或相似。
- 支持从 UI 跳转到对应本地文件。

### 输入

- 已确认 InboxItem
- 已确认摘要
- 已确认 TODO
- 手动备忘
- 用户配置的 Obsidian Vault 路径

### 输出

- Note
- Markdown 文件
- Obsidian 双链
- 笔记写入日志

### MVP 优先级

高。MVP 需要可以生成 Obsidian Markdown 笔记。

## 2.7 Graph Builder Agent

### 定位

负责抽取人、项目、概念、任务、文档、资料之间的关系，并生成轻量知识图谱数据。

### 需要做的事情

- 从 Markdown Frontmatter 和双链中解析实体关系。
- 从摘要、备忘、TODO 和文档中抽取实体。
- 支持实体类型：Person、Project、Topic、Concept、Document、Task、Memo、Resource。
- 支持关系类型：belongs_to、mentions、depends_on、owns、related_to、derived_from、blocked_by、references。
- 为每条关系保留证据来源和置信度。
- 避免未确认或低质量内容大量进入图谱。
- 为图谱视图提供节点和边数据。

### 输入

- Note
- Task
- SourceEvent
- Markdown 双链
- Frontmatter

### 输出

- Entity
- Relation
- 图谱节点和边
- 图谱构建日志

### MVP 优先级

低到中。MVP 可先生成 Obsidian 双链；V0.3 再做独立图谱视图和实体关系表增强。

## 2.8 Feishu Sync Agent

### 定位

负责把确认后的摘要、工作记录、周报和知识笔记写入飞书文档草稿。

### 需要做的事情

- 支持用户选择目标飞书文档或模板。
- 将本地知识笔记转为飞书文档草稿。
- 将群聊摘要、TODO 和周报内容生成飞书草稿。
- 写入前展示预览。
- 用户确认后再写入飞书。
- 保存飞书文档链接、同步时间和同步状态。
- 避免自动覆盖用户编辑过的飞书文档。

### 输入

- 已确认摘要
- 已确认 Note
- 已确认 TODO
- 飞书文档配置
- 用户确认动作

### 输出

- 飞书文档草稿
- SyncLog
- 飞书文档链接
- 错误和重试记录

### MVP 优先级

低。V0.2 开始建设。

## 2.9 Sanitizer Agent

### 定位

负责识别敏感信息，生成脱敏版本，并确保只有用户确认后的内容才能进入 `publishable/`。

### 需要做的事情

- 检测人名、群名、公司内部项目名、内部链接、代码仓库地址、接口地址、账号、Token、Cookie、密钥、业务数据和飞书链接。
- 为每条敏感命中标记类型、位置、风险等级和替换建议。
- 生成脱敏版 Markdown。
- 展示脱敏 diff。
- 用户确认后写入 `publishable/`。
- 保存源文件、脱敏文件和发布记录的映射。
- 防止未确认内容进入发布目录。

### 输入

- 可发布候选 Note
- 学习资料
- 脱敏规则
- 用户确认动作

### 输出

- 脱敏检测结果
- 脱敏版 Markdown
- 发布候选
- 发布日志

### MVP 优先级

低。V0.3 开始建设；MVP 只需要预留 sensitivity 和 publishable 字段。

## 2.10 GitHub Publish Agent

### 定位

负责将用户确认后的 `publishable/` 目录内容提交并同步到 GitHub。

### 需要做的事情

- 限制同步范围，只允许同步 `publishable/` 目录。
- 在 push 前展示待发布文件和脱敏检查状态。
- 支持本地 Git commit。
- 支持手动 push GitHub。
- 记录提交 hash、同步时间、目标仓库和同步状态。
- GitHub push 必须二次确认。

### 输入

- 已确认脱敏内容
- GitHub 仓库配置
- 用户发布确认

### 输出

- Git commit
- GitHub push 结果
- SyncLog

### MVP 优先级

低。V0.3 开始建设。

## 3. UI 模块需要承接的事情

## 3.1 今日看板

- 展示今日待办。
- 展示最近群聊摘要。
- 展示待确认候选项。
- 展示最近更新知识库条目。
- 展示风险、阻塞、需要跟进的人或项目。
- 支持工作、学习、个人、项目过滤。
- 所有卡片展示来源和更新时间。

## 3.2 收件箱

- 承载所有 AI 生成内容和待处理资料。
- 支持未处理、待确认、已确认、已归档、已忽略、需补充、已发布状态。
- 支持批量确认、批量忽略、修改分类、关联项目、转 TODO、转知识笔记、标记敏感、加入脱敏候选。
- 每条内容展示来源、分类、状态和操作记录。

## 3.3 TODO 中心

- 支持今日待办、本周待办、按项目、按来源、按优先级、已完成视图。
- 支持编辑标题、描述、状态、优先级、截止时间、项目、负责人、相关文档和相关知识点。
- 支持状态流转：候选、待办、进行中、已完成、已取消、已归档。
- 每个 TODO 可回溯来源。

## 3.4 备忘录

- 支持桌面端输入框。
- 支持今日看板快速输入。
- 后续支持全局快捷键小窗。
- 备忘创建后进入收件箱，并触发自动分类、TODO 判断和知识库候选判断。

## 3.5 知识库

- 展示本地 Markdown 知识笔记列表。
- 支持按分区、标签、项目、敏感级别、是否可发布过滤。
- 支持查看笔记详情和来源。
- 支持跳转到 Obsidian 文件。

## 3.6 知识图谱

- 展示基础节点和关系。
- 支持按实体类型、项目、分区过滤。
- 点击节点可查看对应笔记、任务或来源。
- MVP 可先不做复杂图谱，先保证 Obsidian 双链可用。

## 3.7 脱敏发布

- 展示可发布候选。
- 展示脱敏检查结果。
- 展示脱敏 diff。
- 支持确认写入 `publishable/`。
- 支持手动 GitHub 同步。

## 3.8 设置中心

- 配置飞书连接。
- 配置关注群。
- 配置 Obsidian Vault 路径。
- 配置本地数据目录。
- 配置 GitHub 仓库。
- 配置摘要频率。
- 配置模型。
- 配置脱敏规则。
- 查看自动化任务和审计日志。

## 4. 数据层需要做的事情

## 4.1 SQLite 表

MVP 至少需要：

- `source_events`
- `inbox_items`
- `tasks`
- `notes`
- `sync_logs`
- `settings`
- `audit_logs`

V0.3 增加：

- `entities`
- `relations`
- `publish_records`
- `sanitizer_findings`

## 4.2 本地文件目录

建议初始化：

```text
agent-vault/
  private/
    raw_messages/
    work_notes/
    personal_memos/
  obsidian/
    work/
    study/
    projects/
    methods/
    people/
  publishable/
    study_notes/
    sanitized_graph/
  system/
    database.sqlite
    sync_logs/
    prompts/
```

## 4.3 Markdown 写入规范

每条知识笔记必须包含：

```yaml
---
title:
type:
tags:
projects:
source:
source_url:
created_at:
updated_at:
sensitivity:
publishable:
---
```

同时需要保证：

- 文件名稳定、可读、避免重复。
- source 可追溯。
- Obsidian 可打开。
- 双链语法可用。
- 敏感字段不会进入公开目录。

## 5. 分阶段开发任务

## 5.1 MVP

目标：先做一个本地可用的个人工作台。

### 必做任务

- 搭建桌面端基础 UI。
- 建立本地 SQLite 数据库。
- 建立本地数据目录和 Obsidian Vault 路径配置。
- 实现快捷备忘输入。
- 备忘写入 SourceEvent。
- 备忘进入收件箱。
- 实现收件箱列表、详情和状态流转。
- 实现 TODO 候选生成。
- 实现 TODO 中心。
- 实现知识笔记生成。
- 写入 Obsidian 兼容 Markdown。
- 支持手动导入或模拟飞书摘要。
- 所有候选内容默认待确认。

### MVP Agent 范围

- Orchestrator Agent：最小状态机。
- Collector Agent：手动备忘、手动导入。
- Task Extractor Agent：备忘和摘要转 TODO。
- Review Agent：候选审核。
- Knowledge Curator Agent：确认内容转 Markdown。
- Summarizer Agent：可先支持模拟或手动导入内容摘要。

### MVP 不做

- 真实飞书群聊自动采集。
- 飞书文档写入。
- GitHub 自动发布。
- 复杂图谱视图。
- 完整脱敏发布。
- 多端同步。

### MVP 验收

- 用户可以在 3 秒内记录一条备忘。
- 备忘可以进入收件箱。
- 用户可以确认一个 TODO 候选。
- TODO 可以在 TODO 中心查看和编辑。
- 用户可以把确认内容生成 Markdown 知识笔记。
- Obsidian 可以打开生成的笔记并识别 Frontmatter 和双链。
- 每条内容可以看到来源和状态。

## 5.2 V0.2

目标：打通飞书消息和飞书文档。

### 必做任务

- 验证飞书 MCP 或 OpenAPI 连通性。
- 支持配置至少一个关注群。
- 支持关注群采集频率、摘要时间窗口、是否只关注提到我的消息等配置。
- 拉取指定时间窗口群聊消息。
- 生成群聊摘要。
- 从群聊摘要中抽取 TODO。
- 摘要和 TODO 保留原始消息来源索引。
- 支持将确认后的摘要生成飞书文档草稿。
- 写入飞书前展示预览。
- 用户确认后写入飞书文档。
- 保存同步日志和文档链接。

### V0.2 Agent 范围

- Collector Agent：真实飞书群聊采集。
- Summarizer Agent：群聊摘要。
- Task Extractor Agent：群聊 TODO 抽取。
- Feishu Sync Agent：飞书文档草稿和写入。
- Review Agent：飞书相关候选审核。

### V0.2 验收

- 至少一个飞书群可以生成摘要。
- 摘要可以转为 TODO 或知识笔记。
- 用户确认后可以写入飞书文档。
- 飞书采集和写入失败有日志，不丢失本地数据。

## 5.3 V0.3

目标：建设知识图谱和脱敏发布流程。

### 必做任务

- 建立 Entity 和 Relation 表。
- 从笔记、TODO、摘要中抽取实体关系。
- 基于 Obsidian 双链增强图谱关系。
- 实现基础图谱视图。
- 支持按实体类型、项目、分区过滤。
- 建立脱敏规则第一版。
- 检测人名、群名、内部链接、仓库地址、接口地址、账号密钥、飞书链接等敏感信息。
- 生成脱敏版 Markdown。
- 展示脱敏 diff。
- 用户确认后写入 `publishable/`。
- 支持手动 Git commit 和 GitHub push。

### V0.3 Agent 范围

- Graph Builder Agent：实体和关系抽取。
- Sanitizer Agent：脱敏检测和脱敏版本生成。
- GitHub Publish Agent：本地提交和手动推送。
- Review Agent：脱敏结果审核和发布确认。

### V0.3 验收

- 用户可以查看项目、人物、任务、笔记之间的关系。
- 用户可以生成脱敏版学习资料。
- 未确认内容不会进入 `publishable/`。
- GitHub 同步只同步 `publishable/`。
- 每次发布都有源文件、脱敏文件和同步记录。

## 5.4 V1.0

目标：形成稳定的个人 Agent 操作系统。

### 必做任务

- 定时任务管理。
- 周报和月报生成。
- 更完整的图谱问答。
- 自动化规则配置。
- 审计和恢复能力增强。
- 多知识库和多项目管理。
- 更完善的失败重试机制。
- 更完整的模型和 Prompt 配置中心。

## 6. 关键安全边界

以下动作必须经过用户确认：

- 写入飞书文档。
- 批量归档知识库。
- 批量生成公开资料。
- 写入 `publishable/`。
- GitHub push。
- 删除原始数据。
- 修改脱敏规则。

以下内容不得进入知识库或发布目录：

- Token。
- Cookie。
- 密钥。
- 账号密码。
- 未脱敏内部链接。
- 未公开业务数据。
- 未确认的飞书消息和私人备忘。

## 7. 首轮开发建议

建议第一轮开发只围绕 MVP，避免同时接入飞书、图谱和 GitHub。

优先顺序：

1. 本地数据模型和状态机。
2. 桌面端 UI 骨架。
3. 快捷备忘。
4. 收件箱审核。
5. TODO 中心。
6. Obsidian Markdown 写入。
7. 模拟飞书摘要导入。
8. 摘要和 TODO Prompt 第一版。

这样可以先验证个人 Agent 的核心闭环：

```text
输入一条信息
→ 进入收件箱
→ 生成候选 TODO 或知识笔记
→ 用户确认
→ 写入 TODO 中心或 Obsidian
→ 保留来源和审计记录
```
