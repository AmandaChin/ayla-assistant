# Ayla Libra Connector

`agents/libra-connector/` 是给 Ayla 工作台桥接 AI Agent 读取的 Libra 只读能力包。

它不依赖 Codex 全局 skill 目录，也不要求 DataOpen `app_id/app_secret`。默认复用本机 Chrome 已登录态的临时副本，以 Headless Chrome 打开 Libra 页面后调用页面自己的 authenticated Web API，因此不会抢占用户焦点。

## 文件

```text
agents/libra-connector/
  SKILL.md                              # Agent 可读调用说明
  scripts/libra_browser_fetch.mjs       # 只读抓取脚本
  schemas/experiment-list.schema.json   # 脚本 JSON 输出结构
```

## 快速验证

```bash
node agents/libra-connector/scripts/libra_browser_fetch.mjs --limit 5
```

结构化输出：

```bash
node agents/libra-connector/scripts/libra_browser_fetch.mjs --json --limit 5
```

只看运行中实验：

```bash
node agents/libra-connector/scripts/libra_browser_fetch.mjs --json --running-only --limit 50
```

脚本会创建临时 Chrome profile，读取完成后自动清理。

输出行会包含实验 ID、名称、状态、创建时间、起止时间、负责人、反转标签和详情页 URL，方便工作台直接渲染“重点实验”列表。

工作台服务端读取实验后会额外做一层本地 TODO 物化：运行中且非反转实验如果创建时间距当前超过 15 天，会在今日 TODO 里创建一条“实验回收 TODO”，DDL 固定为当天 18:00。同一天按 `libra:experiment:{id}:recycle:{date}` 去重，刷新页面不会重复添加。

如果登录态失效、SSO 或二次验证需要人工介入，再显式打开可见窗口：

```bash
node agents/libra-connector/scripts/libra_browser_fetch.mjs --visible --json --limit 5
```
