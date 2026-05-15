---
name: ayla-orchestrator
description: 将用户输入、飞书 Bot 消息、本地备忘、会议纪要、链接或模型输出整理成 Ayla candidates，校验 payload，并按需写入本地 Ayla 工作台。
---

# Ayla Orchestrator Skill

当外部 AI Agent 需要通过 Inbox 契约和 Ayla 个人工作台通信时，使用这个 Skill。

这个 Skill 只覆盖 Orchestrator 的最小闭环：

1. 读取 Ayla 上下文。
2. 使用 `orchestrator-agent.md` 生成 Ayla ingest payload。
3. 校验 payload。
4. 可选地提交到 `/api/agent/ingest`。

它不负责飞书同步、妙记解析、公开知识脱敏、日报生成、Obsidian 写入细节。这些能力后续应拆成独立 Skill，由 Orchestrator 调用。

## 文件说明

- `orchestrator-agent.md`：AI 可读的 SubAgent 提示词。
- `schemas/ingest-payload.schema.json`：payload 契约。
- `scripts/orchestrator_cli.py`：本地调试工具，支持 prompt 渲染、payload 校验、context 拉取和 ingest。
- `examples/`：样例上下文、样例输入和期望输出。

## 快速开始

用样例输入渲染模型 Prompt：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py render-prompt \
  --input agents/orchestrator/examples/capture-link-and-todo.input.txt \
  --context agents/orchestrator/examples/context.sample.json
```

校验模型输出：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py validate \
  --payload agents/orchestrator/examples/capture-link-and-todo.output.json
```

校验所有已提交样例：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py check-examples
```

读取真实 Ayla 上下文：

```bash
AYLA_AGENT_TOKEN="..." python3 agents/orchestrator/scripts/orchestrator_cli.py fetch-context \
  --base-url "$AYLA_BASE_URL"
```

Mac App 模式下，先通过 `ayla open --wait` 启动，再从 `ayla status` 的 `core.url` 或 `~/Library/Application Support/Ayla/runtime/core-state.json` 读取 `AYLA_BASE_URL`。如果本机 Agent 沙箱拦截 Python 访问 `127.0.0.1`，`fetch-context` 和非 dry-run `ingest` 会在 localhost 场景下回退到本仓库 `server.py` 的本地函数；远端 `base-url` 不会回退。

写入前 dry-run：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py ingest \
  --payload agents/orchestrator/examples/capture-link-and-todo.output.json \
  --base-url "$AYLA_BASE_URL" \
  --token "$AYLA_AGENT_TOKEN" \
  --dry-run
```

提交到 Ayla：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py ingest \
  --payload /path/to/model-output.json \
  --base-url "$AYLA_BASE_URL" \
  --token "$AYLA_AGENT_TOKEN"
```

## 候选类型路由

| candidate type | 使用场景 | storage_target | visibility |
|---|---|---|---|
| `todo` | 提醒、任务、跟进、DDL | `local_state` | `private` 或 `internal` |
| `public_note` | 公开、可迁移的知识或待读资料 | `obsidian_public_vault` | `public` |
| `work_record` | 内部工作上下文、项目事实、实验状态 | `local_state` | `internal` |
| `report_material` | 日报、周报、月报、OKR、复盘素材 | `local_state` | `internal` |
| `memory_candidate` | AI 需要跨会话读取的偏好、规则、项目上下文、工具用法 | `local_state` | `private` 或 `internal` |
| `knowledge_candidate` | 分场景知识库资料，长内容只按需检索 | `local_state` 或 `obsidian_public_vault` | `private` / `internal` / `public` |
| `pinned` | 人看的固定便笺，只进入工作台可视化区 | `local_state` | `private` 或 `internal` |
| `memo` | 暂不确定的普通备忘，先留在 Inbox | `local_state` | `private` 或 `internal` |

`memory_candidate` 必须尽量补齐 `memory_type`、`scenario`、`scope` 和稳定 `key`，例如 `ayla.memory.boundary`。固定便笺不参与 Agent context；需要 AI 读取的长期上下文必须写成 `memory_candidate`。

## 确认策略

- 默认 `requires_confirmation=true`。
- `todo`、`due_at`、`memory_candidate`、外部动作、飞书文档写入、中风险内容需要即时确认。
- 删除、覆盖、公开发布、高风险内容需要二次确认。
- 低风险本地记录可以进入批量确认。
- 内部资料不要路由到 `obsidian_public_vault`。

## 飞书来源路由

- 日历不是长期知识来源，主要用于识别今日 TODO 和提醒。普通会议、公共假期、占位日程、无行动项日程不要生成 `public_note`、`work_record` 或 `report_material`。
- 日历事件只有在包含会前准备、参会提醒、材料确认、会后跟进、当天截止等可执行动作时才生成 `todo`；`storage_target=local_state`，`visibility=private` 或 `internal`，`source_refs` 使用 `lark_calendar:<id>`。
- 如果当前 ingest 契约要求非空 candidates，而日历事件没有 TODO，最多生成低风险私有 `memo` 作为临时 Inbox 信号，明确不进入长期知识库。
- 妙记要优先抽取和本人相关的日程、行动项、待办、负责人指向本人、需要本人跟进或下一次同步的信息，并生成 `todo`。
- 妙记 TODO 的 `content` 要包含会议名、具体动作、相关人和截止时间；`source_refs` 使用 `feishu_minutes:<token>`。没有明确截止时间时 `due_at` 留空，但仍然 `requires_confirmation=true`。
- 妙记里不是本人待办、但可用于周报/OKR/复盘的内容才生成 `report_material`；不要把完整妙记或普通会议背景直接沉淀为长期知识。

## 最小 Skill 拆分原则

后续迭代多个 Skill 时，按“一个 Skill 只解决一个稳定问题”的原则拆分：

| Skill | 最小职责 | 不做什么 |
|---|---|---|
| `ayla-workspace-context` | 读取 `/api/agent/context` 并压缩上下文 | 不生成 candidates |
| `ayla-candidate-ingest` | 校验并提交 `/api/agent/ingest` payload | 不判断业务归类 |
| `ayla-inbox-review` | 解释 InboxItem、确认状态和审核动作 | 不直接写外部系统 |
| `ayla-confirmation-policy` | 根据风险和目标推导确认策略 | 不改 payload 内容 |
| `ayla-quick-memo-organizer` | 处理一句话快速备忘 | 不处理飞书批量同步 |
| `ayla-public-knowledge-router` | 判断内容能否进入公开知识库 | 不负责脱敏发布执行 |
| `ayla-local-work-router` | 路由内部工作记录、会议、实验、报告素材 | 不写公开 Vault |
| `ayla-feishu-source-sync` | 只读采集飞书来源并生成 SourceEvent 候选 | 不做最终落库 |
| `ayla-meeting-minutes-parser` | 解析妙记为决策、TODO、风险、报告素材 | 不直接确认任务 |

Orchestrator 的定位是编排这些最小 Skill，而不是把所有规则都塞进一个大 Prompt。

## 输出规则

模型必须只返回 JSON。如果返回 Markdown、解释性文字或非法 JSON，不要写入 Ayla。先用 `validate` 校验。
