---
name: ayla-meego-connector
description: 给 Ayla 工作台桥接 AI Agent 使用的 Meego 只读调研连接器。用于复用当前 Chrome 授权态读取指定 Meego 页面或工作台可见信息，辅助人工分析需求节点，不自动写入 Ayla TODO。
---

# Ayla Meego Connector Skill

这个 Skill 是 **Ayla 工作台内的 Agent 资产**，不是 Codex 全局 skill。它面向能读取当前仓库并执行本地命令的桥接 AI Agent。

目标链路：

```text
已登录 Chrome profile
-> 临时 profile 副本
-> Meego 工作台
-> 同源 authenticated Web API / 页面结构
-> 页面可见信息 / 网络响应摘要
-> 人工分析需求节点
```

## 使用场景

当用户或上游 Agent 请求以下内容时使用：

- 查询指定 Meego 需求当前节点。
- 检查指定 Meego 页面可见的 DDL、流程节点和基础信息。
- 为需求分析、周报或人工整理提供只读素材。

默认只读。不要用这个 Skill 创建、修改、推进、回滚或评论 Meego 工作项；也不要自动把 Meego 信息写入 Ayla TODO。快捷创建能力需要单独做确认门禁。

## 安全边界

- 不索取、不输入、不打印密码、验证码、cookie、token、Local Storage 明文或完整请求头。
- 只复制浏览器 profile 到临时目录；不要直接自动化修改原始 Chrome profile。
- 默认使用 Headless Chrome，避免抢占用户焦点。
- 如果 SSO 或二次验证拦住流程，让用户显式加 `--visible` 完成人工登录；不要绕过认证。
- 输出只包含待办业务字段，例如 ID、标题、项目、工作项类型、节点、DDL、链接。
- 内部需求状态写入 Ayla 时使用 `visibility=internal`，`storage_target=local_state`。

## 首选执行方式

在工作台仓库根目录执行：

```bash
node agents/meego-connector/scripts/meego_browser_fetch.mjs --limit 5
```

结构化输出：

```bash
node agents/meego-connector/scripts/meego_browser_fetch.mjs --json --limit 20
```

需要人工 SSO 或调试时：

```bash
node agents/meego-connector/scripts/meego_browser_fetch.mjs --visible --json --limit 5
```

环境变量可覆盖默认路径：

```bash
MEEGO_CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MEEGO_CHROME_PROFILE_ROOT="$HOME/Library/Application Support/Google/Chrome"
MEEGO_CHROME_PROFILE="Default"
MEEGO_CHROME_VISIBLE="1" # 仅在需要人工 SSO / 调试时使用
```

## 写入边界

这个 Skill 只作为手动调研入口。工作台不会再通过它主动读取 Meego 并自动落 TODO；如果需要把某个 Meego 需求变成 TODO，必须由用户明确确认后走本地 TODO 创建流程。

## 输出字段

脚本 `--json` 输出：

```json
{
  "route": "chrome-profile-browser-bridge",
  "visible": false,
  "code": 200,
  "count": 5,
  "rows": [
    {
      "id": "6732593994",
      "title": "【生服搜索】Android综搜单双列接口改造回归",
      "project_key": "5bebafa4956c37218e2cf5c3",
      "project_name": "抖音",
      "project_simple_name": "aweme",
      "work_item_type": "story",
      "current_node": "需求交付信息记录",
      "status": "待办",
      "due_at": "",
      "url": "https://meego.larkoffice.com/aweme/story/detail/6732593994?parentUrl=%2Fworkbench"
    }
  ]
}
```

Meego 页面字段会随业务配置变化。脚本优先使用同源接口结构化数据；如果字段不完整，会退回页面可见标题，并用标题 hash 生成稳定本地 ID。
