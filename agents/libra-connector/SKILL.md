---
name: ayla-libra-connector
description: 给 Ayla 工作台桥接 AI Agent 使用的 Libra 只读连接器。用于读取当前浏览器授权身份下的 Libra 实验列表、我负责的实验、实验状态和实验基础信息，并将结果整理为可进入 Ayla 的 work_record 或 report_material 候选。
---

# Ayla Libra Connector Skill

这个 Skill 是 **Ayla 工作台内的 Agent 资产**，不是 Codex 全局 skill。它面向任何能读取当前仓库文件并执行本地命令的桥接 AI Agent。

它的目标是让 Agent 以只读方式访问 Libra 实验状态：

```text
已登录 Chrome profile
-> 临时 profile 副本
-> Libra Web 页面
-> 同源 authenticated Web API
-> 结构化实验列表
-> Ayla candidates / 本地工作记录
```

## 使用场景

当用户或上游 Agent 请求以下内容时使用：

- 当前授权用户的 Libra 实验状态
- 我负责的 Libra 实验列表
- 指定数量的 Libra 实验 ID / 名称 / 状态
- 将 Libra 实验状态整理进 Ayla 工作台
- 为周报、日报、OKR 或实验跟进生成素材

默认只读。不要用这个 Skill 修改、启动、暂停、审批或删除 Libra 实验。

## 安全边界

- 不索取、不输入、不打印密码、验证码、cookie、token、Local Storage 明文或完整请求头。
- 只复制浏览器 profile 到临时目录；不要直接用自动化修改原始 Chrome profile。
- 每次读取后删除临时 profile。临时目录可能包含登录态，不能长期保留。
- 默认使用 Headless Chrome 读取，避免抢占用户焦点。如果 SSO 或二次验证拦住流程，让用户加 `--visible` 打开可见 Chrome 窗口完成登录；不要绕过。
- 输出只包含用户要求的业务字段，例如实验 ID、名称、状态、负责人、创建人、起止时间。
- 内部实验状态写入 Ayla 时使用 `visibility=internal`，`storage_target=local_state` 或 `feishu_doc`，不要写到公开知识库。

## 首选执行方式

在工作台仓库根目录执行：

```bash
node agents/libra-connector/scripts/libra_browser_fetch.mjs --limit 5
```

常用参数：

```bash
node agents/libra-connector/scripts/libra_browser_fetch.mjs --json --limit 10
node agents/libra-connector/scripts/libra_browser_fetch.mjs --profile Default --owner-type my
node agents/libra-connector/scripts/libra_browser_fetch.mjs --app-id -1 --page 1 --page-size 50
node agents/libra-connector/scripts/libra_browser_fetch.mjs --visible --json --limit 5
```

环境变量可覆盖默认路径：

```bash
LIBRA_CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
LIBRA_CHROME_PROFILE_ROOT="$HOME/Library/Application Support/Google/Chrome"
LIBRA_CHROME_PROFILE="Default"
LIBRA_CHROME_VISIBLE="1" # 仅在需要人工 SSO / 调试时使用
```

脚本输出 Markdown 表格；加 `--json` 输出结构化 JSON。

## 浏览器 Profile 选择

优先选择能访问 `data.bytedance.net` 的已登录 profile。

macOS 默认 profile root：

```text
~/Library/Application Support/Google/Chrome
```

可从 `Local State` 的 `profile.info_cache` 判断 profile 与账号名。也可以只检查 cookie 域名数量，不读取 cookie 值：

```bash
sqlite3 "$HOME/Library/Application Support/Google/Chrome/Default/Cookies" \
  "select host_key, count(*) from cookies where host_key like '%data.bytedance.net%' or host_key like '%sso.bytedance.com%' group by host_key;"
```

如果某个 profile 被 SSO 二次验证拦住，可以换另一个已登录 profile，或加 `--visible` 让用户在可见窗口完成登录后重试。

## Libra Web API

页面入口：

```text
https://data.bytedance.net/libra/flights?app_id=-1&owner_type=my&page=1&page_size=50&search_type=fuzzy
```

页面加载后使用的列表接口：

```text
https://data.bytedance.net/datatester/experiment/api/v3/app/-1/experiment?owner_type=my&page=1&page_size=50&search_type=fuzzy
```

脚本会在已登录页面上下文中执行 same-origin fetch：

```javascript
fetch("/datatester/experiment/api/v3/app/-1/experiment?owner_type=my&page=1&page_size=50&search_type=fuzzy", {
  credentials: "include"
}).then(r => r.json())
```

典型返回结构：

```text
data.experiments[]
```

常用字段：

- `id`
- `name`
- `status`
- `owners[].name`
- `creator.name`
- `create_time` / `created_time`（若接口没有显式创建时间，则回退到 `start_time`）
- `start_time`
- `end_time`
- `reversal_type`
- `reversal_key`
- `app_id`

当前验证到的 `status=1` 在页面上展示为 `进行中`。其他状态以接口字段或页面展示为准，不要凭空扩展映射。

脚本会把反转信息标准化为：

- `reversal_type=2` -> `reversal_label=反转实验`
- `reversal_type=1` -> `reversal_label=已开启反转`
- 其他 -> `reversal_label=普通实验`

实验详情链接会优先按 Libra 前端路由生成：

```text
https://data.bytedance.net/datatester/app/{app_id}/experiment/{id}/detail
```

## 写入 Ayla 的建议

查询结果如果要进入 Ayla，使用 `work_record` 或 `report_material`：

```json
{
  "type": "work_record",
  "title": "Libra 实验状态快照",
  "content": "列出当前授权身份下的 Libra 实验 ID、名称、状态和负责人。",
  "storage_target": "local_state",
  "visibility": "internal",
  "tags": ["source/libra", "type/experiment", "topic/work"],
  "source_refs": ["libra:experiment-list:owner_type=my"],
  "risk_level": "low",
  "requires_confirmation": true,
  "confidence": 0.9
}
```

如果用于日报、周报、月报、OKR，则可改为 `report_material`，仍保持 `visibility=internal`。

## 工作台 TODO 物化规则

Ayla 工作台服务端会在读取运行中实验后自动补齐实验回收 TODO：

- 仅处理 `status=1` / `进行中` 且非反转实验的实验。
- `is_reversal=true` 或 `reversal_type in [1, 2]` 的实验不生效此规则。
- 当前时间距离 `created_time` 超过 15 天时，创建“实验回收 TODO”。
- DDL 固定为当天 `18:00`。
- 同一天按 `libra:experiment:{id}:recycle:{date}` 去重，避免刷新页面重复添加。
- 任务仍然写入本地 `tasks`，并保持 `visibility=internal`。

## DataOpen OpenAPI 备选

如果后续工作台配置了 DataOpen 租户凭证，也可以走服务端 OpenAPI：

```text
POST https://data.bytedance.net/dataopen/open-apis/v1/authorization
GET  https://data.bytedance.net/dataopen/open-apis/libra/openapi/v1/open/flight-list/?app=<app>
```

这条路线需要 `app_id/app_secret`、功能包权限和 Libra 应用权限。不要把 Feishu/Lark user auth 误判为 Libra OpenAPI 授权。
