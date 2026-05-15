# Ayla Orchestrator Agent

这个目录把当前写在代码里的编排契约沉淀成一组本地 Agent 资产，让它变成 AI 可读、可调用、可测试的能力包。

它暂时不替代 `server.py`。当前 `server.py` 仍然是状态、审核、确认、落库和审计层；这个目录负责让外部 Agent 稳定地产出 `/api/agent/ingest` 已经支持的 `candidates` 结构。

## 目录内容

```text
agents/orchestrator/
  orchestrator-agent.md              # SubAgent 提示词
  SKILL.md                           # 调用说明
  schemas/ingest-payload.schema.json # JSON 契约
  scripts/orchestrator_cli.py        # 校验、渲染 prompt、读取 context、写入 ingest
  examples/
    context.sample.json
    capture-link-and-todo.input.txt
    capture-link-and-todo.output.json
```

## 最小流程

```text
用户 / 飞书 Bot / 本地备忘
-> Orchestrator Agent prompt
-> Ayla candidates JSON
-> validate 校验
-> POST /api/agent/ingest
-> SourceEvent + InboxItem + AgentRun + Confirmation
-> 用户在工作台审核
```

## 本地校验

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py check-examples
```

## 为模型渲染 Prompt

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py render-prompt \
  --input agents/orchestrator/examples/capture-link-and-todo.input.txt \
  --context agents/orchestrator/examples/context.sample.json
```

把渲染后的 prompt 交给任意能返回 JSON 的模型，然后校验模型输出：

```bash
python3 agents/orchestrator/scripts/orchestrator_cli.py validate --payload /path/to/model-output.json
```

## 提交到正在运行的 Ayla 工作台

Mac App 模式下先启动 Ayla，并从 `ayla status` 的 `core.url` 获取当前地址：

```bash
ayla open --wait
ayla status
```

开发模式也可以继续手动启动固定端口：

```bash
python3 server.py --host 127.0.0.1 --port 5173
```

从工作台设置里复制 Token，然后提交：

```bash
AYLA_AGENT_TOKEN="..." python3 agents/orchestrator/scripts/orchestrator_cli.py ingest \
  --payload /path/to/model-output.json \
  --base-url "$AYLA_BASE_URL"
```

成功后会返回 `source_event_id`、`agent_run_id`、`inbox_item_id` 和 `confirmation_id`。

如果当前 Agent 沙箱拦截 Python 对 `127.0.0.1` 的 HTTP 访问，`orchestrator_cli.py` 会在 `base-url` 为 localhost 时回退到本仓库的 `server.py` 函数读取上下文或提交 ingest；远端 `base-url` 仍只走 HTTP。

## 当前边界

这是第一层 Agent 化资产：

- AI 可读：`orchestrator-agent.md`
- 可调用：`scripts/orchestrator_cli.py`
- 可测试：样例 payload 校验和 `check-examples`
- 与现有后端兼容：输出匹配 `/api/agent/ingest`

当前已经区分人看的工作台和 Agent 读取的本地持久层：

- 固定便笺属于 `HumanWorkspace / pinned_slots`，只给用户看，不注入 `/api/agent/context`。
- `memory_candidate` 确认后写入 `AgentMemory/` 和 `agent_memories`，用于偏好、规则、项目上下文、工具用法等跨会话上下文。
- `knowledge_candidate` / `public_note` / `work_record` 写入分场景知识库和 Markdown 资产，长内容只作为索引和引用提供给 Agent。
- Agent 拉上下文时可使用 `/api/agent/context?scenario=coding&project=ayla` 这样的场景化 context pack。

后续可以继续补真实模型 runner、MCP 包装、Inbox 审核工具和更深的飞书来源解析。

## 后续 Skill 拆分原则

后续不要把所有能力继续堆进 Orchestrator。按最小功能集拆分：

```text
ayla-workspace-context       只读上下文
ayla-candidate-ingest        校验和提交 payload
ayla-inbox-review            解释候选和审核动作
ayla-confirmation-policy     推导确认策略
ayla-quick-memo-organizer    整理一句话快速备忘
ayla-public-knowledge-router 路由公开知识
ayla-local-work-router       路由本地工作沉淀
ayla-agent-memory-curator    提取、合并、过期和确认 AgentMemory
ayla-knowledge-space-router  按 work/coding/research/personal/public 路由知识库
ayla-feishu-source-sync      只读同步飞书来源
ayla-meeting-minutes-parser  解析妙记
```

Orchestrator 只做“理解输入、拆分候选、调用最小 Skill、返回 ingest payload”的总控层。
