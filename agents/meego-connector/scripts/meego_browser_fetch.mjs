#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";

const TARGET_ORIGIN = "https://meego.larkoffice.com";
const DEFAULT_WORKBENCH_URL = `${TARGET_ORIGIN}/workbench?tenant_key=ByteDance`;
const DEFAULT_PROFILE_ROOT = process.env.MEEGO_CHROME_PROFILE_ROOT ||
  path.join(os.homedir(), "Library/Application Support/Google/Chrome");
const DEFAULT_PROFILE = process.env.MEEGO_CHROME_PROFILE || "Default";
const DEFAULT_CHROME = process.env.MEEGO_CHROME_PATH ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const DEFAULT_VISIBLE = ["1", "true", "yes", "on"].includes(String(process.env.MEEGO_CHROME_VISIBLE || "").toLowerCase());

const KNOWN_WORKBENCH_WIDGET_ID = "7456766707806683140";
const KNOWN_TODO_VIEW_ID = "s8UGaRvNg";
const KNOWN_ASSET_KEY = "Asset_4f622957-a060-4dcf-af5d-4beaeb8bbade";

function usage() {
  return `Usage:
  node agents/meego-connector/scripts/meego_browser_fetch.mjs [options]

Options:
  --profile <name>        Chrome profile directory to copy (default: ${DEFAULT_PROFILE})
  --profile-root <path>   Chrome user data root
  --chrome <path>         Chrome executable path
  --url <url>             Meego workbench URL (default: ${DEFAULT_WORKBENCH_URL})
  --limit <n>             rows to print (default: 20)
  --port <n>              remote debugging port (default: auto)
  --visible               show Chrome window for manual SSO/debugging (default: headless)
  --json                  output JSON instead of Markdown
  --keep-temp             keep temporary profile directory for debugging
  --copy-indexeddb        also copy IndexedDB (usually unnecessary and large)
  --help                  show this help
`;
}

function parseArgs(argv) {
  const args = {
    profile: DEFAULT_PROFILE,
    profileRoot: DEFAULT_PROFILE_ROOT,
    chrome: DEFAULT_CHROME,
    url: DEFAULT_WORKBENCH_URL,
    limit: 20,
    port: 0,
    visible: DEFAULT_VISIBLE,
    json: false,
    keepTemp: false,
    copyIndexedDB: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error(`missing value for ${arg}`);
      i += 1;
      return argv[i];
    };
    if (arg === "--profile") args.profile = next();
    else if (arg === "--profile-root") args.profileRoot = next();
    else if (arg === "--chrome") args.chrome = next();
    else if (arg === "--url") args.url = next();
    else if (arg === "--limit") args.limit = Number(next());
    else if (arg === "--port") args.port = Number(next());
    else if (arg === "--visible") args.visible = true;
    else if (arg === "--json") args.json = true;
    else if (arg === "--keep-temp") args.keepTemp = true;
    else if (arg === "--copy-indexeddb") args.copyIndexedDB = true;
    else if (arg === "--help" || arg === "-h") {
      process.stdout.write(usage());
      process.exit(0);
    } else {
      throw new Error(`unknown option: ${arg}`);
    }
  }
  if (!Number.isFinite(args.limit) || args.limit < 1) args.limit = 20;
  return args;
}

function ensureExists(filePath, label) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`${label} not found: ${filePath}`);
  }
}

function run(command, commandArgs) {
  const result = spawnSync(command, commandArgs, { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(`${command} ${commandArgs.join(" ")} failed: ${result.stderr || result.stdout}`);
  }
}

function copyIfExists(source, destDir) {
  if (!fs.existsSync(source)) return false;
  fs.mkdirSync(destDir, { recursive: true });
  run("rsync", ["-a", "--ignore-existing", source, destDir]);
  return true;
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = address && typeof address === "object" ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

function httpJson(port, requestPath, method = "GET") {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: "127.0.0.1", port, path: requestPath, method, timeout: 5000 },
      (res) => {
        let body = "";
        res.on("data", (chunk) => {
          body += chunk;
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(body));
          } catch {
            reject(new Error(`invalid JSON from ${requestPath}: ${body.slice(0, 200)}`));
          }
        });
      },
    );
    req.on("error", reject);
    req.on("timeout", () => req.destroy(new Error(`timeout reading ${requestPath}`)));
    req.end();
  });
}

async function waitForChrome(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      return await httpJson(port, "/json/version");
    } catch (error) {
      lastError = error;
      await sleep(500);
    }
  }
  throw lastError || new Error("Chrome did not start");
}

function wsFrame(text) {
  const payload = Buffer.from(text);
  let header;
  if (payload.length < 126) {
    header = Buffer.alloc(6);
    header[0] = 0x81;
    header[1] = 0x80 | payload.length;
    crypto.randomBytes(4).copy(header, 2);
    for (let i = 0; i < payload.length; i += 1) payload[i] ^= header[2 + (i % 4)];
    return Buffer.concat([header, payload]);
  }
  if (payload.length < 65536) {
    header = Buffer.alloc(8);
    header[0] = 0x81;
    header[1] = 0x80 | 126;
    header.writeUInt16BE(payload.length, 2);
    crypto.randomBytes(4).copy(header, 4);
    for (let i = 0; i < payload.length; i += 1) payload[i] ^= header[4 + (i % 4)];
    return Buffer.concat([header, payload]);
  }
  throw new Error("CDP payload too large");
}

function makeWsParser(onMessage) {
  let buffer = Buffer.alloc(0);
  return (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    while (buffer.length >= 2) {
      const b1 = buffer[1];
      let length = b1 & 0x7f;
      let offset = 2;
      if (length === 126) {
        if (buffer.length < 4) return;
        length = buffer.readUInt16BE(2);
        offset = 4;
      } else if (length === 127) {
        if (buffer.length < 10) return;
        const bigLength = buffer.readBigUInt64BE(2);
        if (bigLength > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("WebSocket frame is too large");
        length = Number(bigLength);
        offset = 10;
      }
      const masked = Boolean(b1 & 0x80);
      let mask;
      if (masked) {
        if (buffer.length < offset + 4) return;
        mask = buffer.subarray(offset, offset + 4);
        offset += 4;
      }
      if (buffer.length < offset + length) return;
      const payload = Buffer.from(buffer.subarray(offset, offset + length));
      if (masked) {
        for (let i = 0; i < payload.length; i += 1) payload[i] ^= mask[i % 4];
      }
      buffer = buffer.subarray(offset + length);
      if (payload.length) onMessage(payload.toString("utf8"));
    }
  };
}

function connectCdp(wsUrl, onEvent = null) {
  const url = new URL(wsUrl);
  let nextId = 0;
  const pending = new Map();
  let readyResolve;
  let readyReject;
  const ready = new Promise((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });
  const socket = net.connect(Number(url.port), url.hostname, () => {
    const key = crypto.randomBytes(16).toString("base64");
    socket.write(
      `GET ${url.pathname} HTTP/1.1\r\n` +
        `Host: ${url.host}\r\n` +
        "Upgrade: websocket\r\n" +
        "Connection: Upgrade\r\n" +
        `Sec-WebSocket-Key: ${key}\r\n` +
        "Sec-WebSocket-Version: 13\r\n\r\n",
    );
  });
  let handshake = false;
  const parseWs = makeWsParser((message) => {
    const payload = JSON.parse(message);
    if (payload.id && pending.has(payload.id)) {
      const { resolve, reject, timeout } = pending.get(payload.id);
      clearTimeout(timeout);
      pending.delete(payload.id);
      if (payload.error) reject(new Error(JSON.stringify(payload.error)));
      else resolve(payload);
    } else if (payload.method && onEvent) {
      onEvent(payload);
    }
  });
  socket.on("data", (chunk) => {
    if (!handshake) {
      const text = chunk.toString("utf8");
      const index = text.indexOf("\r\n\r\n");
      if (index < 0) return;
      handshake = true;
      readyResolve();
      const rest = Buffer.from(text.slice(index + 4));
      if (rest.length) parseWs(rest);
      return;
    }
    parseWs(chunk);
  });
  socket.on("error", (error) => {
    readyReject(error);
  });
  return {
    ready,
    send(method, params = {}, timeoutMs = 30000) {
      const id = ++nextId;
      socket.write(wsFrame(JSON.stringify({ id, method, params })));
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`timeout: ${method}`));
        }, timeoutMs);
        pending.set(id, { resolve, reject, timeout });
      });
    },
    close() {
      socket.destroy();
    },
  };
}

async function waitForMeegoTab(port, targetUrl) {
  const deadline = Date.now() + 45000;
  let created = false;
  while (Date.now() < deadline) {
    const tabs = await httpJson(port, "/json/list");
    const meegoTab = tabs.find((tab) => tab.type === "page" && tab.url && tab.url.includes("meego.larkoffice.com"));
    if (meegoTab && !/about:blank/.test(meegoTab.url || "")) return meegoTab;
    const ssoTab = tabs.find((tab) => tab.type === "page" && /sso\.bytedance\.com|accounts\.google\.com|login/.test(tab.url || ""));
    if (!created && !ssoTab) {
      created = true;
      await httpJson(port, `/json/new?${encodeURIComponent(targetUrl)}`, "PUT");
    }
    await sleep(1000);
  }
  const tabs = await httpJson(port, "/json/list").catch(() => []);
  const visible = tabs
    .filter((tab) => tab.type === "page")
    .map((tab) => `${tab.title || "(untitled)"} ${tab.url || ""}`)
    .join("\n");
  throw new Error(`Meego tab did not become available. Open pages:\n${visible}`);
}

function pageReaderSource() {
  return async function pageReader(options) {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const origin = "https://meego.larkoffice.com";
    const knownWidgetId = options.knownWidgetId;
    const knownTodoViewId = options.knownTodoViewId;
    const knownAssetKey = options.knownAssetKey;
    const limit = Number(options.limit) || 20;

    async function waitForWorkbenchText() {
      const deadline = Date.now() + 20000;
      while (Date.now() < deadline) {
        const text = document.body?.innerText || "";
        if (/我的待办|我的工作/.test(text)) return text;
        await sleep(500);
      }
      return document.body?.innerText || "";
    }

    async function fetchJson(url, init = {}) {
      try {
        const response = await fetch(url, {
          credentials: "include",
          headers: {
            "Accept": "application/json, text/plain, */*",
            ...(init.headers || {}),
          },
          ...init,
        });
        const text = await response.text();
        let data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch {
          data = null;
        }
        return { ok: response.ok, status: response.status, url, data, text: data ? "" : text.slice(0, 500) };
      } catch (error) {
        return { ok: false, status: 0, url, error: String(error && error.message ? error.message : error) };
      }
    }

    function textValue(value) {
      if (value === null || value === undefined) return "";
      if (typeof value === "string") return value.trim();
      if (typeof value === "number" || typeof value === "boolean") return String(value);
      return "";
    }

    function plain(value) {
      return textValue(value).replace(/\s+/g, " ").trim();
    }

    function isObject(value) {
      return value && typeof value === "object" && !Array.isArray(value);
    }

    function findFirstByKeys(root, keys) {
      const wanted = new Set(keys.map((key) => key.toLowerCase()));
      const seen = new WeakSet();
      const stack = [root];
      let inspected = 0;
      while (stack.length && inspected < 6000) {
        const current = stack.pop();
        inspected += 1;
        if (!current || typeof current !== "object") continue;
        if (seen.has(current)) continue;
        seen.add(current);
        if (isObject(current)) {
          for (const [key, value] of Object.entries(current)) {
            if (wanted.has(key.toLowerCase())) {
              const text = textValue(value);
              if (text) return text;
            }
            if (value && typeof value === "object") stack.push(value);
          }
        } else if (Array.isArray(current)) {
          for (const item of current) {
            if (item && typeof item === "object") stack.push(item);
          }
        }
      }
      return "";
    }

    function collectByKeys(root, keys, limitCount = 20) {
      const wanted = new Set(keys.map((key) => key.toLowerCase()));
      const seen = new WeakSet();
      const values = [];
      const stack = [root];
      let inspected = 0;
      while (stack.length && inspected < 8000 && values.length < limitCount) {
        const current = stack.pop();
        inspected += 1;
        if (!current || typeof current !== "object") continue;
        if (seen.has(current)) continue;
        seen.add(current);
        if (isObject(current)) {
          for (const [key, value] of Object.entries(current)) {
            if (wanted.has(key.toLowerCase())) {
              const text = textValue(value);
              if (text && !values.includes(text)) values.push(text);
            }
            if (value && typeof value === "object") stack.push(value);
          }
        } else if (Array.isArray(current)) {
          for (const item of current) {
            if (item && typeof item === "object") stack.push(item);
          }
        }
      }
      return values;
    }

    function collectObjects(root, predicate, limitCount = 200) {
      const seen = new WeakSet();
      const rows = [];
      const stack = [root];
      let inspected = 0;
      while (stack.length && inspected < 12000 && rows.length < limitCount) {
        const current = stack.pop();
        inspected += 1;
        if (!current || typeof current !== "object") continue;
        if (seen.has(current)) continue;
        seen.add(current);
        if (isObject(current) && predicate(current)) rows.push(current);
        const values = Array.isArray(current) ? current : Object.values(current);
        for (const value of values) {
          if (value && typeof value === "object") stack.push(value);
        }
      }
      return rows;
    }

    function firstDirect(obj, keys) {
      if (!isObject(obj)) return "";
      for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(obj, key)) {
          const value = plain(obj[key]);
          if (value) return value;
        }
      }
      const lowered = Object.fromEntries(Object.keys(obj).map((key) => [key.toLowerCase(), key]));
      for (const key of keys) {
        const actual = lowered[key.toLowerCase()];
        if (actual) {
          const value = plain(obj[actual]);
          if (value) return value;
        }
      }
      return "";
    }

    function deepTextByKeys(obj, keys) {
      const direct = firstDirect(obj, keys);
      if (direct) return direct;
      return findFirstByKeys(obj, keys);
    }

    function isWorkItemId(value) {
      const text = plain(value);
      return /^\d{6,18}$/.test(text) || /^[A-Za-z0-9_-]{8,80}$/.test(text);
    }

    function titleLooksUseful(title) {
      const text = plain(title);
      if (text.length < 4 || text.length > 140) return false;
      if (/^\d+$/.test(text)) return false;
      if (/^https?:\/\//.test(text)) return false;
      if (/^(我的工作|我的待办|我的关注|我参与的|我创建的|我的已办|任务|本周到期|已超期|未排期|需求|缺陷|全部|筛选|排序|新建|查看全部|模板中心|自定义工作台)$/.test(text)) return false;
      if (/^(早上好|中午好|下午好|晚上好|你好)[，,]/.test(text)) return false;
      if (/^\d{1,2}\s*月\s*\d{1,2}\s*日/.test(text)) return false;
      if (/使用 MCP 连接飞书项目/.test(text)) return false;
      return /[\u4e00-\u9fa5A-Za-z]/.test(text);
    }

    function stableHash(text) {
      let hash = 2166136261;
      for (let i = 0; i < text.length; i += 1) {
        hash ^= text.charCodeAt(i);
        hash = Math.imul(hash, 16777619);
      }
      return `title_${(hash >>> 0).toString(16)}`;
    }

    function pickTitle(obj) {
      const direct = firstDirect(obj, [
        "title",
        "name",
        "work_item_name",
        "workItemName",
        "workitem_name",
        "summary",
        "display_name",
        "displayName",
        "content",
      ]);
      if (titleLooksUseful(direct)) return direct;
      const candidates = collectByKeys(obj, [
        "title",
        "name",
        "text",
        "label",
        "value",
        "display",
        "display_value",
        "displayValue",
      ], 12);
      return candidates.find(titleLooksUseful) || "";
    }

    function pickId(obj) {
      const direct = firstDirect(obj, [
        "work_item_id",
        "workItemID",
        "workItemId",
        "workitem_id",
        "demand_id",
        "story_id",
        "issue_id",
        "object_id",
        "id",
        "ID",
      ]);
      if (isWorkItemId(direct)) return direct;
      const values = collectByKeys(obj, ["work_item_id", "workItemID", "id", "ID"], 20);
      return values.find(isWorkItemId) || "";
    }

    function normalizeDue(value) {
      const text = plain(value);
      if (!text) return "";
      if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.replace(" ", "T").slice(0, 16);
      if (/^\d{13}$/.test(text) || /^\d{10}$/.test(text)) {
        const n = Number(text);
        if (Number.isFinite(n)) {
          const date = new Date(n > 10000000000 ? n : n * 1000);
          if (!Number.isNaN(date.getTime())) {
            const pad = (v) => String(v).padStart(2, "0");
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
          }
        }
      }
      return text.length <= 40 ? text : "";
    }

    function buildUrl(row) {
      if (row.url && !/undefined/.test(row.url)) return row.url;
      if (!row.id || row.id.startsWith("title_")) return `${origin}/workbench`;
      const project = row.project_simple_name || row.project_key || "aweme";
      const type = row.work_item_type || "story";
      return `${origin}/${encodeURIComponent(project)}/${encodeURIComponent(type)}/detail/${encodeURIComponent(row.id)}?parentUrl=%2Fworkbench`;
    }

    function normalizeCandidate(obj, source) {
      const title = pickTitle(obj);
      if (!titleLooksUseful(title)) return null;
      const id = pickId(obj) || stableHash(title);
      const row = {
        id,
        title,
        project_key: deepTextByKeys(obj, ["project_key", "projectKey", "project_id", "projectID"]),
        project_name: deepTextByKeys(obj, ["project_name", "projectName", "project", "space_name"]),
        project_simple_name: deepTextByKeys(obj, ["project_simple_name", "projectSimpleName", "simple_name", "projectKey"]),
        work_item_type: deepTextByKeys(obj, ["work_item_type", "workItemType", "workitem_type", "type_key", "type"]),
        current_node: deepTextByKeys(obj, ["current_node", "currentNode", "node_name", "nodeName", "status_node", "statusNode"]),
        status: deepTextByKeys(obj, ["status", "status_name", "statusName", "state", "state_name"]) || "待办",
        due_at: normalizeDue(deepTextByKeys(obj, ["due_at", "dueAt", "deadline", "due_date", "dueDate", "end_time", "endTime", "schedule"])),
        overdue: /超期|逾期|overdue/i.test(JSON.stringify(obj).slice(0, 3000)),
        url: deepTextByKeys(obj, ["url", "link", "href", "detail_url", "detailUrl"]),
        source,
      };
      row.url = buildUrl(row);
      return row;
    }

    function extractRowsFromPayload(payload, source) {
      if (!payload) return [];
      const objects = collectObjects(payload, (obj) => {
        const title = pickTitle(obj);
        if (!titleLooksUseful(title)) return false;
        const serialized = JSON.stringify(obj).slice(0, 2500);
        return /work_item|workItem|需求|任务|story|demand|detail|node|status|project/i.test(serialized);
      }, 160);
      return objects.map((obj) => normalizeCandidate(obj, source)).filter(Boolean);
    }

    function visibleRowsFromDom() {
      const rows = [];
      const seen = new Set();
      const selectors = [
        "a[href*='/detail/']",
        "[role='row']",
        "tr",
        "[data-row-key]",
        "[data-testid*='row']",
      ];
      for (const selector of selectors) {
        for (const el of Array.from(document.querySelectorAll(selector)).slice(0, 120)) {
          const text = plain(el.innerText || el.textContent || "");
          const title = text.split(/\n/).map(plain).find(titleLooksUseful) || text;
          if (!titleLooksUseful(title)) continue;
          const href = el.href || el.querySelector?.("a[href]")?.href || "";
          const idMatch = href.match(/detail\/(\d{6,18})/);
          const id = idMatch?.[1] || el.getAttribute("data-row-key") || stableHash(title);
          const key = `${id}:${title}`;
          if (seen.has(key)) continue;
          seen.add(key);
          rows.push({
            id,
            title,
            project_key: "",
            project_name: "",
            project_simple_name: "",
            work_item_type: "",
            current_node: "",
            status: /超期|逾期/.test(text) ? "超期" : "待办",
            due_at: "",
            overdue: /超期|逾期/.test(text),
            url: href && !/undefined/.test(href) ? href : `${origin}/workbench`,
            source: "dom",
          });
        }
      }
      if (rows.length) return rows;
      const text = document.body?.innerText || "";
      const todoIndex = text.indexOf("我的待办");
      if (todoIndex < 0) return rows;
      const nextIndexes = ["我的已办", "我的关注", "我参与的", "我创建的"]
        .map((label) => text.indexOf(label, todoIndex + 1))
        .filter((index) => index > todoIndex);
      const endIndex = nextIndexes.length ? Math.min(...nextIndexes) : text.length;
      const lines = text.slice(todoIndex, endIndex).split(/\n+/).map(plain).filter(Boolean);
      for (const line of lines) {
        if (!titleLooksUseful(line)) continue;
        const key = stableHash(line);
        if (seen.has(key)) continue;
        seen.add(key);
        rows.push({
          id: key,
          title: line,
          project_key: "",
          project_name: "",
          project_simple_name: "",
          work_item_type: "",
          current_node: "",
          status: "待办",
          due_at: "",
          overdue: false,
          url: `${origin}/workbench`,
          source: "dom-text",
        });
        if (rows.length >= limit) break;
      }
      return rows;
    }

    function mergeRows(rowGroups) {
      const merged = [];
      const byId = new Map();
      const byTitle = new Map();
      for (const rows of rowGroups) {
        for (const row of rows) {
          if (!row || !titleLooksUseful(row.title)) continue;
          const idKey = row.id && !row.id.startsWith("title_") ? row.id : "";
          const titleKey = row.title.toLowerCase();
          const existing = (idKey && byId.get(idKey)) || byTitle.get(titleKey);
          if (existing) {
            for (const [key, value] of Object.entries(row)) {
              if ((existing[key] === "" || existing[key] === undefined || existing[key] === false) && value) {
                existing[key] = value;
              }
            }
            existing.url = buildUrl(existing);
            continue;
          }
          row.url = buildUrl(row);
          merged.push(row);
          if (idKey) byId.set(idKey, row);
          byTitle.set(titleKey, row);
        }
      }
      return merged.slice(0, limit);
    }

    function workbenchCountsFromText(text) {
      const labels = ["我的工作", "我的待办", "我的关注", "我参与的", "我创建的", "任务", "本周到期", "已超期", "未排期"];
      const counts = {};
      for (const label of labels) {
        const match = text.match(new RegExp(`${label}\\s*(\\d+)`));
        if (match) counts[label] = Number(match[1]);
      }
      return counts;
    }

    const pageText = await waitForWorkbenchText();
    await sleep(4000);
    const info = await fetchJson("/goapi/v4/workbench/info");
    const infoData = info.data?.data || info.data || {};
    const tenantKey = findFirstByKeys(infoData, ["tenant_key", "tenantKey", "tenant"]) || "ByteDance";
    const userKey = findFirstByKeys(infoData, ["user_key", "userKey", "user_id", "userID", "id"]);
    const assetKey = findFirstByKeys(infoData, ["asset_key", "assetKey"]) || knownAssetKey;
    const widgetIds = collectByKeys(infoData, ["widget_id", "widgetID", "widgetId"], 8);
    if (!widgetIds.includes(knownWidgetId)) widgetIds.push(knownWidgetId);

    const requests = { info: { ok: info.ok, status: info.status } };
    let todoPayload = null;
    if (tenantKey && assetKey && userKey) {
      const todoUrl = `/goapi/v5/platform/workbench/todo?tenant_key=${encodeURIComponent(tenantKey)}&asset_key=${encodeURIComponent(assetKey)}&user_key=${encodeURIComponent(userKey)}`;
      todoPayload = await fetchJson(todoUrl);
      requests.workbench_todo = { ok: todoPayload.ok, status: todoPayload.status };
    }

    const worksheetPayloads = [];
    for (const widgetId of widgetIds.slice(0, 4)) {
      const worksheet = await fetchJson(`/goapi/v4/workbench/worksheet?skip_view_config=true&widget_id=${encodeURIComponent(widgetId)}`);
      requests[`worksheet_${widgetId}`] = { ok: worksheet.ok, status: worksheet.status };
      if (worksheet.ok && worksheet.data) worksheetPayloads.push(worksheet.data);
    }

    let todoViewId = knownTodoViewId;
    for (const payload of worksheetPayloads) {
      const todoObjects = collectObjects(payload, (obj) => JSON.stringify(obj).includes("我的待办"), 20);
      for (const obj of todoObjects) {
        const candidate = firstDirect(obj, ["id", "view_id", "viewID", "data_source_id", "dataSourceID", "key", "value"]);
        if (/^[A-Za-z0-9_-]{6,80}$/.test(candidate)) {
          todoViewId = candidate;
          break;
        }
      }
      if (todoViewId !== knownTodoViewId) break;
    }

    let worksheetData = null;
    if (todoViewId) {
      worksheetData = await fetchJson(`/goapi/v3/worksheet/data_source/${encodeURIComponent(todoViewId)}`);
      requests.worksheet_data_source = { ok: worksheetData.ok, status: worksheetData.status, id: todoViewId };
    }

    const groups = [
      extractRowsFromPayload(todoPayload?.data, "workbench-todo-api"),
      ...worksheetPayloads.map((payload) => extractRowsFromPayload(payload, "worksheet-api")),
      extractRowsFromPayload(worksheetData?.data, "worksheet-data-source"),
      visibleRowsFromDom(),
    ];
    const rows = mergeRows(groups);
    const greeting = pageText.split(/\n+/).map(plain).find((line) => /早上好|中午好|下午好|晚上好|你好/.test(line)) || "";
    return {
      code: 200,
      message: rows.length ? "ok" : "no rows extracted",
      count: rows.length,
      account: {
        greeting,
        tenant_key: tenantKey,
        user_key: userKey,
        asset_key: assetKey,
      },
      counters: workbenchCountsFromText(pageText),
      requests,
      rows,
    };
  };
}

function nodeText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function nodePlain(value) {
  return nodeText(value).replace(/\s+/g, " ").trim();
}

function nodeIsObject(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function nodeFirstDirect(obj, keys) {
  if (!nodeIsObject(obj)) return "";
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const value = nodePlain(obj[key]);
      if (value) return value;
    }
  }
  const lowered = Object.fromEntries(Object.keys(obj).map((key) => [key.toLowerCase(), key]));
  for (const key of keys) {
    const actual = lowered[key.toLowerCase()];
    if (actual) {
      const value = nodePlain(obj[actual]);
      if (value) return value;
    }
  }
  return "";
}

function nodeCollectObjects(root, predicate, limitCount = 200) {
  const seen = new WeakSet();
  const rows = [];
  const stack = [root];
  let inspected = 0;
  while (stack.length && inspected < 30000 && rows.length < limitCount) {
    const current = stack.pop();
    inspected += 1;
    if (!current || typeof current !== "object") continue;
    if (seen.has(current)) continue;
    seen.add(current);
    if (nodeIsObject(current) && predicate(current)) rows.push(current);
    const values = Array.isArray(current) ? current : Object.values(current);
    for (const value of values) {
      if (value && typeof value === "object") stack.push(value);
    }
  }
  return rows;
}

function nodeFindFirstByKeys(root, keys) {
  const wanted = new Set(keys.map((key) => key.toLowerCase()));
  const seen = new WeakSet();
  const stack = [root];
  let inspected = 0;
  while (stack.length && inspected < 30000) {
    const current = stack.pop();
    inspected += 1;
    if (!current || typeof current !== "object") continue;
    if (seen.has(current)) continue;
    seen.add(current);
    if (nodeIsObject(current)) {
      for (const [key, value] of Object.entries(current)) {
        if (wanted.has(key.toLowerCase())) {
          const text = nodePlain(value);
          if (text) return text;
        }
        if (value && typeof value === "object") stack.push(value);
      }
    } else if (Array.isArray(current)) {
      for (const item of current) {
        if (item && typeof item === "object") stack.push(item);
      }
    }
  }
  return "";
}

function nodeFindObjectByKey(root, keyName) {
  const seen = new WeakSet();
  const stack = [root];
  let inspected = 0;
  while (stack.length && inspected < 30000) {
    const current = stack.pop();
    inspected += 1;
    if (!current || typeof current !== "object") continue;
    if (seen.has(current)) continue;
    seen.add(current);
    if (nodeIsObject(current) && nodeIsObject(current[keyName])) return current[keyName];
    const values = Array.isArray(current) ? current : Object.values(current);
    for (const value of values) {
      if (value && typeof value === "object") stack.push(value);
    }
  }
  return null;
}

function normalizeNetworkDue(value) {
  const text = nodePlain(value);
  if (!text) return "";
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.replace(" ", "T").slice(0, 16);
  if (/^\d{4}\/\d{2}\/\d{2}/.test(text)) return text.replaceAll("/", "-").replace(" ", "T").slice(0, 16);
  if (/^\d{13}$/.test(text) || /^\d{10}$/.test(text)) {
    const n = Number(text);
    if (Number.isFinite(n)) {
      const date = new Date(n > 10000000000 ? n : n * 1000);
      if (!Number.isNaN(date.getTime())) {
        const pad = (v) => String(v).padStart(2, "0");
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
      }
    }
  }
  return "";
}

function usefulNodeName(value) {
  const text = nodePlain(value);
  if (!text || text.length > 36) return false;
  if (/^\d+$/.test(text) || /^https?:\/\//.test(text)) return false;
  return /需求|开发|测试|交付|发布|验收|排期|评审|设计|上线|完成|待办|处理中/.test(text);
}

function pickNetworkCurrentNode(root) {
  const direct = nodeFindFirstByKeys(root, [
    "current_node",
    "currentNode",
    "current_node_name",
    "currentNodeName",
    "node_name",
    "nodeName",
    "state_node_name",
    "status_node_name",
    "workflow_node_name",
  ]);
  if (usefulNodeName(direct)) return direct;
  const nodeObjects = nodeCollectObjects(root, (obj) => {
    const text = JSON.stringify(obj).slice(0, 1200);
    return /node|节点|状态|workflow|state/i.test(text);
  }, 120);
  for (const obj of nodeObjects) {
    const name = nodeFirstDirect(obj, ["name", "title", "label", "display_value", "displayValue", "value"]);
    if (usefulNodeName(name)) return name;
  }
  return "";
}

function pickNetworkDue(root) {
  const direct = nodeFindFirstByKeys(root, [
    "due_at",
    "dueAt",
    "deadline",
    "due_date",
    "dueDate",
    "plan_end_time",
    "planned_end_time",
    "finish_time",
    "end_time",
    "endTime",
  ]);
  const directDate = normalizeNetworkDue(direct);
  if (directDate) return directDate;
  const dueObjects = nodeCollectObjects(root, (obj) => {
    const label = nodeFirstDirect(obj, ["name", "title", "label", "field_name", "fieldName"]);
    return /DDL|截止|到期|完成时间|计划完成|排期|deadline|due/i.test(label);
  }, 80);
  for (const obj of dueObjects) {
    const value = nodeFirstDirect(obj, ["value", "display_value", "displayValue", "text", "date", "timestamp"]);
    const date = normalizeNetworkDue(value);
    if (date) return date;
  }
  return "";
}

function collectNetworkFieldCandidates(root, pattern, limit = 20) {
  const objects = nodeCollectObjects(root, (obj) => {
    const label = nodeFirstDirect(obj, ["name", "title", "label", "field_name", "fieldName", "key"]);
    return pattern.test(label);
  }, 120);
  return objects.slice(0, limit).map((obj) => ({
    label: nodeFirstDirect(obj, ["name", "title", "label", "field_name", "fieldName", "key"]),
    value: nodeFirstDirect(obj, ["value", "display_value", "displayValue", "text", "date", "timestamp", "name"]),
  })).filter((item) => item.label || item.value);
}

function extractNetworkRows(captures, limit) {
  const rows = [];
  const seen = new Set();
  for (const capture of captures) {
    const fullUIDatas = nodeFindObjectByKey(capture.json, "fullUIDatas");
    if (!fullUIDatas) continue;
    for (const [id, perView] of Object.entries(fullUIDatas)) {
      if (!perView || typeof perView !== "object" || seen.has(id)) continue;
      const data = { id, views: Object.values(perView) };
      const row = {
        id,
        title: nodeFindFirstByKeys(data, ["title", "name", "work_item_name", "workItemName", "summary"]),
        project_key: nodeFindFirstByKeys(data, ["project_key", "projectKey", "project_id", "projectID"]),
        project_name: nodeFindFirstByKeys(data, ["project_name", "projectName", "project"]),
        project_simple_name: nodeFindFirstByKeys(data, ["project_simple_name", "projectSimpleName", "simple_name"]),
        work_item_type: nodeFindFirstByKeys(data, ["work_item_type", "workItemType", "type_key", "type"]),
        current_node: pickNetworkCurrentNode(data),
        status: nodeFindFirstByKeys(data, ["status", "status_name", "statusName", "state_name"]) || "待办",
        due_at: pickNetworkDue(data),
        overdue: /超期|逾期|overdue/i.test(JSON.stringify(data).slice(0, 5000)),
        url: "",
        source: "network-full-ui-data",
      };
      if (row.id && !row.id.startsWith("title_")) {
        const project = row.project_simple_name || row.project_key || "aweme";
        const type = row.work_item_type || "story";
        row.url = `${TARGET_ORIGIN}/${encodeURIComponent(project)}/${encodeURIComponent(type)}/detail/${encodeURIComponent(row.id)}?parentUrl=%2Fworkbench`;
      }
      if (process.env.MEEGO_DEBUG_FIELDS) {
        row.debug_due_candidates = collectNetworkFieldCandidates(data, /DDL|截止|到期|完成时间|计划完成|排期|deadline|due|time|时间/i, 12);
        row.debug_node_candidates = collectNetworkFieldCandidates(data, /节点|node|状态|status|state/i, 12);
      }
      seen.add(id);
      rows.push(row);
      if (rows.length >= limit) return rows;
    }
  }
  return rows;
}

function mergeNetworkRows(domRows, networkRows, limit) {
  const rows = [];
  const count = Math.max(domRows.length, networkRows.length);
  for (let index = 0; index < count && rows.length < limit; index += 1) {
    const dom = domRows[index] || {};
    const network = networkRows[index] || {};
    const networkTitle = network.title && network.title !== network.current_node && !usefulNodeName(network.title)
      ? network.title
      : "";
    const title = dom.title || networkTitle;
    if (!title) continue;
    rows.push({
      ...dom,
      ...Object.fromEntries(Object.entries(network).filter(([, value]) => value !== "" && value !== undefined && value !== null)),
      title,
      status: network.status || dom.status || "待办",
      source: network.source || dom.source || "",
    });
  }
  return rows;
}

async function readMeegoTodos(wsUrl, args) {
  const pendingResponses = new Map();
  const captures = [];
  const capturePromises = [];
  let cdp;
  const isRelevantUrl = (url) => /\/goapi\/v5\/search\/full_ui_data\/mget|\/goapi\/v1\/workitem\/v1\/demand_fetch|\/goapi\/v5\/workitem\/v1\/demand_fetch|\/goapi\/v5\/platform\/workbench\/todo|\/goapi\/v3\/worksheet\/data_source/i.test(url || "");
  cdp = connectCdp(wsUrl, (event) => {
    if (event.method === "Network.responseReceived") {
      const response = event.params?.response || {};
      if (isRelevantUrl(response.url)) {
        pendingResponses.set(event.params.requestId, {
          url: response.url,
          status: response.status,
        });
      }
    }
    if (event.method === "Network.loadingFinished" && pendingResponses.has(event.params?.requestId)) {
      const info = pendingResponses.get(event.params.requestId);
      pendingResponses.delete(event.params.requestId);
      const promise = cdp.send("Network.getResponseBody", { requestId: event.params.requestId }, 12000)
        .then((payload) => {
          const body = payload.result?.body || "";
          if (!body || payload.result?.base64Encoded) return;
          try {
            captures.push({ ...info, json: JSON.parse(body) });
          } catch {
            // Ignore non-JSON relevant responses.
          }
        })
        .catch(() => {});
      capturePromises.push(promise);
    }
  });
  await cdp.ready;
  try {
    await cdp.send("Network.enable");
    await cdp.send("Page.enable").catch(() => {});
    await cdp.send("Runtime.enable");
    await cdp.send("Page.reload", { ignoreCache: true }).catch(() => {});
    await sleep(7000);
    const expression = `(${pageReaderSource().toString()})(${JSON.stringify({
      limit: args.limit,
      knownWidgetId: KNOWN_WORKBENCH_WIDGET_ID,
      knownTodoViewId: KNOWN_TODO_VIEW_ID,
      knownAssetKey: KNOWN_ASSET_KEY,
    })})`;
    const result = await cdp.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    }, 60000);
    const value = result.result?.result?.value;
    if (!value) throw new Error("Meego fetch returned no value");
    await Promise.allSettled(capturePromises);
    const networkRows = extractNetworkRows(captures, Number(args.limit));
    if (networkRows.length) {
      value.rows = mergeNetworkRows(value.rows || [], networkRows, Number(args.limit));
      value.count = value.rows.length;
      value.network = {
        captured: captures.length,
        full_ui_rows: networkRows.length,
      };
    }
    return value;
  } finally {
    cdp.close();
  }
}

function markdownTable(rows) {
  const lines = ["| ID | Meego 待办 | 项目 | 节点 | DDL |", "|---:|---|---|---|---|"];
  for (const row of rows) {
    const title = String(row.title || "").replaceAll("|", "\\|");
    const link = row.url ? `[${title}](${row.url})` : title;
    const project = row.project_name || row.project_simple_name || row.project_key || "-";
    lines.push(`| ${row.id} | ${link} | ${String(project).replaceAll("|", "\\|")} | ${String(row.current_node || row.status || "-").replaceAll("|", "\\|")} | ${row.due_at || "-"} |`);
  }
  return `${lines.join("\n")}\n`;
}

async function closeBrowser(browserWsUrl) {
  const cdp = connectCdp(browserWsUrl);
  await cdp.ready;
  try {
    await cdp.send("Browser.close", {}, 5000).catch(() => {});
  } finally {
    cdp.close();
  }
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function removeTempDir(tempRoot) {
  let lastError;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      fs.rmSync(tempRoot, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
      return;
    } catch (error) {
      lastError = error;
      await sleep(300);
    }
  }
  throw lastError;
}

function chromeLaunchArgs(args, tempRoot, port, targetUrl) {
  const launchArgs = [
    `--user-data-dir=${tempRoot}`,
    `--profile-directory=${args.profile}`,
    `--remote-debugging-port=${port}`,
    "--no-first-run",
    "--no-default-browser-check",
  ];
  if (args.visible) {
    launchArgs.push("--new-window");
  } else {
    launchArgs.push(
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--window-size=1360,960",
    );
  }
  launchArgs.push(targetUrl);
  return launchArgs;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  ensureExists(args.chrome, "Chrome executable");
  ensureExists(args.profileRoot, "Chrome profile root");
  ensureExists(path.join(args.profileRoot, args.profile), "Chrome profile");

  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ayla-meego-chrome-"));
  const tempProfileDir = path.join(tempRoot, args.profile);
  fs.mkdirSync(tempProfileDir, { recursive: true });
  let chrome;
  let browserWsUrl = "";

  try {
    copyIfExists(path.join(args.profileRoot, "Local State"), tempRoot);
    for (const item of ["Preferences", "Secure Preferences", "Cookies"]) {
      copyIfExists(path.join(args.profileRoot, args.profile, item), tempProfileDir);
    }
    copyIfExists(path.join(args.profileRoot, args.profile, "Network", "Cookies"), path.join(tempProfileDir, "Network"));
    for (const item of ["Local Storage", "Session Storage"]) {
      copyIfExists(path.join(args.profileRoot, args.profile, item), tempProfileDir);
    }
    if (args.copyIndexedDB) copyIfExists(path.join(args.profileRoot, args.profile, "IndexedDB"), tempProfileDir);

    const port = args.port || (await getFreePort());
    chrome = spawn(args.chrome, chromeLaunchArgs(args, tempRoot, port, args.url), {
      stdio: ["ignore", "ignore", "ignore"],
    });

    const version = await waitForChrome(port);
    browserWsUrl = version.webSocketDebuggerUrl;
    const tab = await waitForMeegoTab(port, args.url);
    const result = await readMeegoTodos(tab.webSocketDebuggerUrl, args);
    const output = { route: "chrome-profile-browser-bridge", visible: args.visible, ...result };

    if (args.json) {
      process.stdout.write(JSON.stringify(output, null, 2));
      process.stdout.write("\n");
    } else {
      process.stdout.write(markdownTable(output.rows || []));
    }
  } finally {
    if (browserWsUrl) {
      await closeBrowser(browserWsUrl).catch(() => {});
    }
    if (chrome && !chrome.killed) {
      chrome.kill("SIGTERM");
    }
    await sleep(800);
    if (!args.keepTemp) {
      await removeTempDir(tempRoot);
    } else {
      process.stderr.write(`Temporary profile kept at: ${tempRoot}\n`);
    }
  }
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
});
