# OpenClaw 写入 Ayla 工作台 MVP 接入说明

目标：飞书 Bot 收到一句话后，由 OpenClaw Agent 理解和调用工具，再把结构化结果同步到本机 Ayla 工作台。

## 1. 工作台地址

本机运行：

```text
http://127.0.0.1:5173
```

写入接口需要 Token。Token 可在工作台「设置中心 → OpenClaw 写入 Token」复制。

## 2. 获取上下文

OpenClaw Agent 在整理前先拉取本地上下文：

```bash
curl -L -sS \
  -H "X-Ayla-Agent-Token: $AYLA_AGENT_TOKEN" \
  http://127.0.0.1:5173/api/agent/context
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
  -X POST http://127.0.0.1:5173/api/agent/ingest \
  -H "Content-Type: application/json" \
  -H "X-Ayla-Agent-Token: $AYLA_AGENT_TOKEN" \
  -d '{
    "source": "feishu_bot",
    "raw_input": "https://example.com 这个内容不错",
    "intent": "note",
    "title": "网页文章标题",
    "summary": "这篇内容主要讲个人知识管理的收集、整理和复盘。",
    "content": "整理后的正文内容",
    "category": "学习",
    "tags": ["待读", "知识管理"],
    "project": "",
    "source_url": "https://example.com",
    "due_at": "",
    "priority": "normal",
    "sensitivity": "private",
    "publishable": false,
    "confidence": 0.91,
    "needs_review": true,
    "reasoning_hint": "用户说内容不错且包含网页链接，归为学习资料候选。"
  }'
```

## 4. intent 约定

```text
todo    -> TODO 候选
note    -> 知识候选
pinned  -> 固定便笺候选
memo    -> 普通备忘归档候选
```

兼容别名：

```text
task/reminder -> todo
link/knowledge/document -> note
fixed/pin/sticky -> pinned
```

## 5. Agent Prompt 建议

可以在 OpenClaw Agent 的系统提示中加入：

```text
你是用户的个人记忆整理 Agent。收到飞书 Bot 消息后：

1. 先调用 ayla workspace context 获取本地上下文。
2. 判断用户输入属于 todo、note、pinned 或 memo。
3. 如果包含网页链接、飞书文档、文章、视频或资料链接，先调用合适的网页/文档解析 MCP。
4. 生成结构化 JSON，字段包含 raw_input、intent、title、summary、content、category、tags、project、source_url、due_at、priority、sensitivity、publishable、confidence、reasoning_hint。
5. 调用 ayla workspace ingest 写入本机工作台。
6. 默认 needs_review=true，除非用户明确要求直接保存。
7. 回复用户一句简短确认：已放入今日增量整理，并说明归类结果。
```

## 6. MVP 部署建议

第一版建议 OpenClaw 和 Ayla 工作台都跑在同一台 Mac：

```text
飞书 Bot
→ 本机 OpenClaw Agent
→ http://127.0.0.1:5173/api/agent/ingest
→ Ayla 工作台今日增量整理
```

如果 OpenClaw 跑在远端服务器，服务器不能直接访问本机 `127.0.0.1`，需要改成云端 relay、内网穿透，或让工作台主动轮询远端结果。
