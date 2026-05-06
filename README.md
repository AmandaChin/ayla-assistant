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
- 快速备忘包含网页链接时，会自动解析网页标题、描述和正文片段，生成智能标题、摘要和归档建议，并在 Obsidian 笔记中保留 `source_url`。
- 今日增量整理，可先编辑标题、内容、目标分类、项目、截止时间等，再按天批量确认自动分类结果。
- 收件箱候选审核。
- TODO 候选确认和 TODO 中心编辑。
- TODO 到期提醒，可通过浏览器通知接入 macOS 通知中心。
- 模拟飞书摘要导入。
- OpenClaw Agent 可通过带 Token 的 `/api/agent/context` 和 `/api/agent/ingest` 把飞书 Bot 整理结果同步到今日增量整理。
- 确认内容写入 Obsidian 兼容 Markdown。
- 知识库笔记删除，会同步删除本地 Markdown 文件。
- 基于笔记、标签、项目和 TODO 的轻量图谱。
- 设置中心和审计日志。

## 启动

```bash
python3 server.py --host 127.0.0.1 --port 5173
```

打开：

```text
http://127.0.0.1:5173
```

## 本地数据

运行后会自动创建：

```text
agent-vault/
  private/
  obsidian/
  publishable/
  system/
    database.sqlite
```

其中 `agent-vault/` 已写入 `.gitignore`，用于存放个人数据、SQLite 数据库和生成的 Markdown 笔记。

## MVP 流程

```text
输入备忘或导入摘要
→ 自动分类进入今日整理
→ 每天批量确认增量分类
→ TODO 进入 TODO 中心
→ 知识笔记写入 agent-vault/obsidian
→ 审计日志记录操作
```

## 后续开发方向

- 替换为 Tauri + React + TypeScript 桌面端。
- 将当前 SQLite 表结构固化为迁移脚本。
- 接入飞书 MCP 或 OpenAPI。
- 增加真实群聊采集、摘要和 TODO 抽取。
- 将当前本地网页解析器替换或增强为 MCP 文档解析器和真实 AI 摘要模型。
- 将 `openclaw-workspace-writer.md` 中的 HTTP 调用封装成正式 OpenClaw skill 或 MCP server。
- 增加脱敏检测、发布目录和 GitHub 同步。
