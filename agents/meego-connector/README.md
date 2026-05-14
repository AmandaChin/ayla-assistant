# Ayla Meego Connector

`agents/meego-connector/` 是给 Ayla 工作台桥接 AI Agent 使用的 Meego 只读调研能力包。

它不依赖 Codex 全局 skill 目录。默认复用本机 Chrome 已登录态的临时副本，以 Headless Chrome 打开 Meego 页面后读取可见信息和页面网络响应摘要，因此不会抢占用户焦点。

## 文件

```text
agents/meego-connector/
  SKILL.md                           # Agent 可读调用说明
  scripts/meego_browser_fetch.mjs    # 只读抓取脚本
  schemas/todo-list.schema.json      # 脚本 JSON 输出结构
```

## 快速验证

```bash
node agents/meego-connector/scripts/meego_browser_fetch.mjs --limit 5
```

结构化输出：

```bash
node agents/meego-connector/scripts/meego_browser_fetch.mjs --json --limit 5
```

脚本会创建临时 Chrome profile，读取完成后自动清理。输出行尽量包含 Meego 工作项 ID、标题、项目、类型、当前节点、状态、DDL 和详情页 URL。

工作台不会通过这个 Skill 主动读取 Meego 并自动落 TODO。需要把某个 Meego 需求变成 TODO 时，必须由用户明确确认后再走本地 TODO 创建流程。

如果登录态失效、SSO 或二次验证需要人工介入，再显式打开可见窗口：

```bash
node agents/meego-connector/scripts/meego_browser_fetch.mjs --visible --json --limit 5
```
