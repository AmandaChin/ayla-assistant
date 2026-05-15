# Ayla 个人 Agent 工作台

这是基于 `personal-agent-prd.md` 和 `personal-agent-work-breakdown.md` 落地的 MVP 版本。

## 当前能力

- 本地 Web 工作台。
- Python 标准库 HTTP 服务，无需安装第三方依赖。
- SQLite 本地数据存储。
- 快捷备忘输入。
- 苹果 Notes 风格固定便笺，可长期记录工作信息、待读书清单、常用资料等。
- 快速备忘支持记录模式切换，可直接新增固定便笺。
- 快速备忘自动分类为 TODO、知识笔记或普通备忘。
- 快速备忘包含飞书文档或网页链接时，会优先用 `lark-cli docs +fetch --api-version v2` 读取飞书正文，普通网页回退到本地网页解析；工作台展示简短链接总结，真正落库的 Study / 知识资产会把抓取到的正文转换成完整 Markdown，并保留 `source_url`。
- Agent 编排视图，展示 Collector / Orchestrator / Task Extractor / Review / Knowledge Curator 等角色、连接器优先级、待确认动作和最近 AgentRun。
- `/api/agent/ingest` 支持文档设计里的多候选 `candidates` Schema，可一次写入 TODO、Agent 记忆、公开知识、工作沉淀、固定便笺和总结素材候选。
- 新增 `agent_runs` 和 `confirmations` 审计表，低风险本地写入走日维度批量确认，TODO、外部写入、高风险动作进入即时或二次确认。
- 新增独立 `AgentMemory` 本地持久层，固定便笺只给人看；AI 读的长期偏好、规则、项目上下文和工具用法通过 `memory_candidate` 确认后写入 AgentMemory。
- 新增 `knowledge_spaces` / `knowledge_categories`，按 work、coding、research、personal、public 分场景管理知识库分类存储。
- 公开知识与内部工作资料分流：公开、可迁移内容写 `PublicKnowledgeVault`；工作沉淀、会议、实验、TODO 和内部资料默认写 `LocalWorkState`。
- 今日增量整理，可先编辑标题、内容、目标分类、项目、截止时间等，再按天批量确认自动分类结果。
- 每日备忘归档会把归档摘要写成本地 Markdown 资产，并用本机模型 CLI 生成一句话标题；今日看板卡片可直接跳转到对应资产。
- 收件箱候选审核。
- TODO 候选确认和 TODO 中心编辑。
- TODO 到期提醒，可通过浏览器通知接入 macOS 通知中心。
- 模拟飞书摘要导入。
- OpenClaw Agent 可通过带 Token 的 `/api/agent/context` 和 `/api/agent/ingest` 把飞书 Bot 整理结果同步到今日增量整理。
- 个人设置支持工作账号绑定：通过 `lark-cli auth login` 的 Device Flow 生成扫码授权，绑定后工作台 profile、SourceEvent owner、TODO assignee 和 Markdown frontmatter 会落到授权人的真实飞书身份。
- 飞书数据源适配：通过本机 `lark-cli` 只读同步日历和妙记，自动落入 `SourceEvent`、收件箱候选和确认队列。
- 快速备忘可切换到 `model_cli` 整理引擎，优先调用本机 `codex exec` 或 `claude -p` 输出 candidates，失败时自动回退本地规则。
- 确认内容写入 Obsidian 兼容 Markdown。
- 知识库笔记删除，会同步删除本地 Markdown 文件。
- 基于笔记、标签、项目和 TODO 的轻量图谱。
- 设置中心和审计日志。

## Mac App 与 CLI

```bash
./ayla install --force
```

安装后会生成：

```text
~/Applications/Ayla.app
~/.local/bin/ayla
~/Library/Application Support/Ayla/
```

如果 `~/.local/bin` 已在 `PATH` 中，后续可以直接执行：

```bash
ayla open
ayla status
ayla doctor
ayla update
ayla capture "记一下今天要跟进项目排期"
ayla sync lark --days 7
```

如果 `ayla` 还不在 `PATH`，先用完整路径执行：

```bash
~/.local/bin/ayla status
```

`Ayla.app` 是无第三方依赖的轻量原生 macOS 客户端：使用 Swift + AppKit + WebKit `WKWebView` 构建窗口。点击或执行 `ayla open` 后，App 会自动拉起 Ayla Core，选择一个可用的本地端口，写入运行状态文件，并在 App 窗口内展示工作台。用户不再需要记住或维护固定的 `5173` 端口，也不会再跳到外部浏览器。

运行状态和日志位于：

```text
~/Library/Application Support/Ayla/
  app/                  # 已安装的 Ayla Core 和 Web 资源
  data/                 # SQLite、LocalWorkState、PublicKnowledgeVault
  logs/core.log         # 后台 Core 日志
  runtime/core-state.json
  install.json
```

App 构建链路位于：

```text
macos/AylaClient/main.swift        # 原生 AppKit/WKWebView 客户端
macos/AylaClient/AppIcon.png       # Ayla App 图标源文件
scripts/build_macos_client.sh      # swiftc 构建 .app bundle
scripts/generate_app_icon.py       # 无第三方依赖的图标生成脚本
packaging/macos/app_launcher.py    # App 内部用于启动 Ayla Core 的 Python bootstrap
```

构建脚本会把 `macos/AylaClient/AppIcon.png` 转成 `Ayla.app/Contents/Resources/AylaAppIcon.icns`，并写入 `CFBundleIconFile`。如果图标源文件不存在，构建时会自动调用 `scripts/generate_app_icon.py` 重新生成。

## 后续更新

本地仓库有功能改动后，推荐直接从当前仓库重装一次：

```bash
git pull --ff-only origin master
./ayla install --force
ayla stop
ayla open
```

如果已经通过 `./ayla install --force` 安装过，且 `~/.local/bin` 在 `PATH` 中，也可以用已安装的 CLI 更新：

```bash
ayla update
ayla stop
ayla open
```

`ayla update` 会读取 `~/Library/Application Support/Ayla/install.json` 里记录的 `source_root`，从该源码目录重新复制 Ayla Core、Web 资源、Swift 客户端和构建脚本，并重新构建 `~/Applications/Ayla.app`。个人数据不会被覆盖，仍保留在：

```text
~/Library/Application Support/Ayla/data/
```

更新规则：

- 改 `web/`、`server.py`、`agents/`、`packaging/` 或 CLI 逻辑：执行 `ayla update` 或 `./ayla install --force`，再重启 App。
- 改 `macos/AylaClient/main.swift`、`scripts/build_macos_client.sh` 或 App 图标：同样执行 `ayla update` 或 `./ayla install --force`，因为需要重新构建 `.app`。
- 如果后续改到 SQLite 表结构，需要补迁移脚本；当前安装更新只负责替换程序文件，不会删除用户数据。

## 开发模式

开发时仍然可以直接启动当前仓库里的 Web MVP：

```bash
python3 server.py --host 127.0.0.1 --port 5173
```

开发模式会继续使用仓库内的 `agent-vault/`。Mac App 模式会通过 `AYLA_HOME` 把个人数据写到 `~/Library/Application Support/Ayla/data/`。

## 本地数据

运行后会自动创建本地资产目录。正常从项目目录运行时路径是 `./agent-vault/`；在 Codex worktree 中运行时，会优先落到真实项目目录：

```text
/Users/bytedance/Documents/ayla assistant/agent-vault/
```

开发态也可以通过 `AYLA_PROJECT_ROOT`、`AYLA_VAULT_ROOT` 覆盖资产位置。Mac App 模式会通过 `AYLA_HOME` 把个人数据写到 `~/Library/Application Support/Ayla/data/`。

```text
agent-vault/
  AgentMemory/
    global/
    projects/
    tools/
    skills/
    episodes/
  LocalWorkState/
    inbox/
    tasks/
    work_records/
    meeting_actions/
    experiment_snapshots/
    reports/
    audit_log/
  PublicKnowledgeVault/
    00_Inbox/
    10_Concepts/
    20_Resources/
    30_Methods/
    40_Tools/
    50_ReadLater/
    90_Archive/
  runtime/
  private/
  obsidian/
  publishable/
  system/
    database.sqlite
```

其中 `agent-vault/` 已写入 `.gitignore`，用于存放开发态个人数据、SQLite 数据库和生成的 Markdown 笔记，不上传到 GitHub。正式 Mac App 安装后的数据默认不写入仓库，而是写入 `~/Library/Application Support/Ayla/data/`。

## MVP 流程

```text
输入备忘或导入摘要
→ 进入 SourceEvent / InboxItem
→ model_cli / 外部 Agent 生成结构化 candidates
→ 按风险进入批量确认 / 即时确认 / 二次确认
→ 每天批量确认增量分类
→ memory_candidate 写 AgentMemory，knowledge_candidate 写分场景知识库
→ TODO 进入 TODO 中心，本地工作记录写 LocalWorkState
→ 公开知识写 PublicKnowledgeVault
→ 审计日志记录操作
```

## model_cli 整理引擎

在「设置中心」里将「整理引擎」切到 `真实模型 CLI` 后，快速备忘会走：

```text
快速备忘
→ 本机 codex / claude CLI
→ 输出 Ayla candidates JSON
→ /api/agent/ingest
→ AgentRun + Confirmation + 收件箱候选
```

内置命令：

```text
codex  -> codex exec --sandbox read-only --ephemeral ...
claude -> claude -p --output-format text ...
```

如果需要接自定义模型，可在「自定义模型命令」里填写一条从 stdin 读取 prompt、向 stdout 输出 JSON 的命令。模型输出解析失败、命令超时或命令不可用时，系统会自动回退到本地规则，并在审计日志里记录 `model_cli_fallback`。

## Orchestrator Agent 资产

`agents/orchestrator/` 已沉淀为第一版 AI 可读、可调用、可测试的 Orchestrator 资产：

```text
agents/orchestrator/
  orchestrator-agent.md              # SubAgent 提示词
  SKILL.md                           # 调用说明
  schemas/ingest-payload.schema.json # candidates 契约
  scripts/orchestrator_cli.py        # prompt 渲染、payload 校验、context 拉取、ingest
  examples/                          # 样例输入、上下文和输出
```

本地校验：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py check-examples
```

这个资产的边界是：Orchestrator 负责把输入整理成 Ayla candidates JSON；`server.py` 仍然负责 SourceEvent、InboxItem、AgentRun、Confirmation、落库和审计。

## 链接总结 Skill

`agents/link-summary/SKILL.md` 记录了飞书文档 / 网页链接的抓取顺序和总结格式：飞书文档优先 `lark-cli docs +fetch --api-version v2`，动态网页可接 browser-use / 浏览器 MCP，普通网页回退本地 HTTP 解析；看板总结保持短句、关键点和 `source_url`，落库资产保留完整 Markdown 正文，用于后续搜索、复盘和跳回原链接。

后续迭代多个 Skill 时按最小功能集拆分：上下文读取、payload 校验提交、Inbox 审核、确认策略、快速备忘整理、公开知识路由、本地工作沉淀路由、飞书来源同步和妙记解析都应拆成独立 Skill；Orchestrator 只做总控和 candidates 生成。

## 飞书数据源

在「设置中心」里启用「飞书数据源」后，工作台会把本机 `lark-cli` 封装成只读 Connector：

```text
lark-cli calendar +agenda
lark-cli minutes +search --owner-ids me
lark-cli minutes +search --participant-ids me
→ SourceEvent
→ InboxItem 候选
→ Confirmation 人工确认
```

后端提供两个接口：

```text
GET  /api/connectors/lark/status
POST /api/connectors/lark/sync
POST /api/connectors/lark/bind/start
POST /api/connectors/lark/bind/complete
POST /api/connectors/lark/bind/claim
```

`status` 用于检查命令是否存在、当前飞书 user 身份是否有效，以及是否具备 `calendar:calendar.event:read`、`minutes:minutes.search:read` 和妙记全文 / TODO 抽取相关敏感权限。`bind/start` 会调用 `lark-cli auth login --no-wait --json` 生成扫码授权信息，`bind/complete` 轮询完成授权并写入工作账号归属，`bind/claim` 可把已经完成的本机 `lark-cli` 认证绑定为当前工作台身份。`sync` 会按日期范围同步日历和妙记；重复同步会按飞书事件 ID、妙记 token 或 URL 去重，不会重复创建候选项。

同步结果默认只写本地工作台，不会写回飞书。日历会生成 `work_record_candidate`，妙记会生成 `report_material_candidate`，都需要在今日整理或收件箱里确认后才进入本地工作库。

## 后续开发方向

- 将当前 AppKit/WKWebView 客户端继续增强为菜单栏、全局快捷键、通知和登录项完整体验，CLI 命令和本地数据目录保持兼容。
- 将当前 SQLite 表结构固化为迁移脚本。
- 增加真实飞书群聊采集、摘要和 TODO 抽取。
- 将当前本地网页解析器替换或增强为 MCP 文档解析器和真实 AI 摘要模型。
- 将 `openclaw-workspace-writer.md` 中的 HTTP 调用封装成正式 OpenClaw skill 或 MCP server。
- 增加脱敏检测、发布目录和 GitHub 同步。
