# OpenClaw 写入 Ayla 工作台 MVP 接入说明

目标：飞书 Bot 收到一句话后，由 OpenClaw Agent 理解和调用工具，再把结构化结果同步到本机 Ayla 工作台。

## 1. 工作台地址

Mac App 模式下不要写死端口。先打开 Ayla：

```bash
ayla open --wait
ayla status
```

`ayla status` 会返回当前 `core.url`。同一台 Mac 上的 Agent 也可以直接读取：

```text
~/Library/Application Support/Ayla/runtime/core-state.json
```

开发模式才固定使用 `http://127.0.0.1:5173`。

写入接口需要 Token。Token 可在工作台「设置中心 → OpenClaw 写入 Token」复制。

## 2. 获取上下文

OpenClaw Agent 在整理前先拉取本地上下文：

```bash
curl -L -sS \
  -H "X-Ayla-Agent-Token: $AYLA_AGENT_TOKEN" \
  "$AYLA_BASE_URL/api/agent/context"
```

返回内容包含：

- 分类和 Obsidian 分区。
- 已有项目。
- 已有标签。
- 固定便笺标题。
- 最近 TODO。
- 最近知识笔记。
- 当前整理规则。

## 3. 写入整理结果

OpenClaw Agent 整理完成后调用：

```bash
curl -L -sS \
  -X POST "$AYLA_BASE_URL/api/agent/ingest" \
  -H "Content-Type: application/json" \
  -H "X-Ayla-Agent-Token: $AYLA_AGENT_TOKEN" \
  -d '{
    "source": "feishu_bot",
    "raw_input": "https://example.com 这个内容不错；另外明天提醒我看实验数据",
    "intent": "capture",
    "summary": "用户同时转存了一条公开学习资料，并提出一个带时间的 TODO。",
    "candidates": [
      {
        "type": "public_note",
        "title": "网页文章标题",
        "content": "这篇内容主要讲个人知识管理的收集、整理和复盘。",
        "storage_target": "obsidian_public_vault",
        "visibility": "public",
        "tags": ["type/resource", "topic/knowledge-management", "status/readlater"],
        "source_url": "https://example.com",
        "source_refs": ["feishu_msg_xxx"],
        "risk_level": "low",
        "requires_confirmation": true,
        "confidence": 0.91
      },
      {
        "type": "todo",
        "title": "看实验数据",
        "content": "明天回收并查看实验数据。",
        "storage_target": "local_state",
        "visibility": "internal",
        "due_at": "2026-05-08",
        "priority": "medium",
        "risk_level": "low",
        "requires_confirmation": true,
        "confidence": 0.86
      }
    ],
    "questions": [],
    "tool_actions": []
  }'
```

返回会包含本次 `agent_run_id`、每个候选落成的 `inbox_item_id`、对应 `confirmation_id`、落库目标、可见性和确认策略。

旧版单候选字段仍兼容：`intent/title/content/category/tags/project/source_url/due_at/priority/sensitivity/publishable/confidence/needs_review` 会被自动转换为一个 `candidates` 条目。

## 4. candidate 约定

```text
todo            -> TODO 候选，默认 local_state，通常即时确认
public_note     -> 公开知识候选，写 PublicKnowledgeVault，必须 visibility=public
work_record     -> 工作沉淀候选，写 LocalWorkState，不进入公开 Vault
report_material -> 周报/月报/季度总结素材，写 LocalWorkState/reports
memory_candidate -> AI 读的长期记忆候选，确认后写 AgentMemory
knowledge_candidate -> 分场景知识库候选，确认后写 KnowledgeBase/Markdown
pinned          -> 人看的固定便笺候选，只属于工作台可视化区，不进入 Agent context
memo            -> 普通备忘归档候选
```

`memory_candidate` 建议额外带上：

```text
memory_type     -> preference / rule / project_context / workflow / tool_usage / decision / user_profile / writing_style
scenario        -> global / coding / work / research / writing / planning / daily
scope           -> global / project / repo / tool / skill / person
key             -> 稳定去重键，例如 ayla.memory.boundary
```

`storage_target` 取值：

```text
local_state
feishu_doc
obsidian_public_vault
```

`visibility` 取值：

```text
private
internal
public
```

确认策略由工作台根据 `type`、`storage_target`、`risk_level`、`due_at` 和 `tool_actions` 自动判断：

```text
batch_confirm   -> 低风险本地写入，进入今日增量整理
instant_confirm -> TODO、DDL、AgentMemory、飞书文档写入、外部工具动作
double_confirm  -> 删除、覆盖、公开发布和 high risk
```

## 5. Orchestrator Agent 资产

当前工程已经把 Agent Prompt、Schema、样例和本地校验脚本沉淀到：

```text
agents/orchestrator/
```

优先使用这个目录里的资产，而不是在 OpenClaw 里手写一份分叉 Prompt：

```text
agents/orchestrator/orchestrator-agent.md
agents/orchestrator/SKILL.md
agents/orchestrator/schemas/ingest-payload.schema.json
agents/orchestrator/scripts/orchestrator_cli.py
```

本地验证模型输出：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py validate --payload /path/to/model-output.json
```

写入前 dry-run：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py ingest \
  --payload /path/to/model-output.json \
  --base-url "$AYLA_BASE_URL" \
  --token "$AYLA_AGENT_TOKEN" \
  --dry-run
```

## 6. Agent Prompt 建议

可以在 OpenClaw Agent 的系统提示中加入：

```text
你是用户的个人记忆整理 Agent。收到飞书 Bot 消息后：

1. 先调用 ayla workspace context 获取本地上下文。
2. 判断用户输入是否包含多个候选，例如一个链接资料 + 一个 TODO。
3. 如果包含网页链接、飞书文档、文章、视频或资料链接，先调用合适的网页/文档解析 MCP。
4. 生成结构化 JSON，顶层包含 raw_input、intent、summary、candidates、questions、tool_actions。
5. 每个 candidate 必须包含 type、title、content、storage_target、visibility、tags、source_refs、risk_level、requires_confirmation。
6. 公司内部资料、会议纪要、实验状态、Meego/Libra/GitHub 工作状态默认 visibility=internal，storage_target=local_state 或 feishu_doc，不写 obsidian_public_vault。
7. 只有可公开、可迁移、适合系统学习的内容才允许 type=public_note，visibility=public，storage_target=obsidian_public_vault。
8. 稳定偏好、规则、项目上下文、工具用法才生成 memory_candidate，并补 memory_type、scenario、scope、key。
9. 固定便笺只给人看，不作为 Agent 记忆；需要 AI 读取的长期上下文必须走 memory_candidate。
10. 调用 ayla workspace ingest 写入本机工作台。
11. 默认 requires_confirmation=true，除非用户明确要求直接保存且风险为 low。
12. 回复用户一句简短确认：已放入待确认队列，并说明归类结果。
```

## 7. MVP 部署建议

第一版建议 OpenClaw 和 Ayla 工作台都跑在同一台 Mac：

```text
飞书 Bot
→ 本机 OpenClaw Agent
→ Ayla Core 当前 core.url /api/agent/ingest
→ Ayla 工作台今日增量整理
```

如果 OpenClaw 跑在远端服务器，服务器不能直接访问本机 `127.0.0.1`，需要改成云端 relay、内网穿透，或让工作台主动轮询远端结果。

## 8. 本地 model_cli adapter

工作台自己的「快速备忘」也可以直接桥接真实模型。设置中心里将「整理引擎」改为 `真实模型 CLI` 后，`/api/memos` 会优先调用本机模型命令：

```text
codex  -> codex exec --sandbox read-only --ephemeral ...
claude -> claude -p --output-format text ...
```

模型命令需要从 stdin 接收 prompt，并向 stdout 输出符合本文 `candidates` 约定的 JSON。解析成功后，工作台会复用 `/api/agent/ingest` 写入 AgentRun 和候选项；解析失败、超时或命令不存在时，会自动回退到本地规则，并写入 `model_cli_fallback` 审计日志。

如果要接其他模型，可以在设置中心填写「自定义模型命令」，例如：

```text
my-local-model --json
```

只要该命令满足“stdin 输入 prompt、stdout 输出 JSON”，就可以作为整理引擎。
