#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";

const TARGET_ORIGIN = "https://data.bytedance.net";
const DEFAULT_PROFILE_ROOT = process.env.LIBRA_CHROME_PROFILE_ROOT ||
  path.join(os.homedir(), "Library/Application Support/Google/Chrome");
const DEFAULT_PROFILE = process.env.LIBRA_CHROME_PROFILE || "Default";
const DEFAULT_CHROME = process.env.LIBRA_CHROME_PATH ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const DEFAULT_VISIBLE = ["1", "true", "yes", "on"].includes(String(process.env.LIBRA_CHROME_VISIBLE || "").toLowerCase());
const STATUS_LABELS = {
  1: "进行中",
};

function usage() {
  return `Usage:
  node agents/libra-connector/scripts/libra_browser_fetch.mjs [options]

Options:
  --profile <name>        Chrome profile directory to copy (default: ${DEFAULT_PROFILE})
  --profile-root <path>   Chrome user data root
  --chrome <path>         Chrome executable path
  --app-id <id>           Libra app id in URL/API (default: -1)
  --owner-type <type>     owner_type query value (default: my)
  --page <n>              page number (default: 1)
  --page-size <n>         page size (default: 50)
  --limit <n>             rows to print (default: 5)
  --port <n>              remote debugging port (default: auto)
  --running-only          only output experiments that are currently running
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
    appId: "-1",
    ownerType: "my",
    page: "1",
    pageSize: "50",
    limit: 5,
    port: 0,
    runningOnly: false,
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
    else if (arg === "--app-id") args.appId = next();
    else if (arg === "--owner-type") args.ownerType = next();
    else if (arg === "--page") args.page = next();
    else if (arg === "--page-size") args.pageSize = next();
    else if (arg === "--limit") args.limit = Number(next());
    else if (arg === "--port") args.port = Number(next());
    else if (arg === "--running-only") args.runningOnly = true;
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
  if (!Number.isFinite(args.limit) || args.limit < 1) args.limit = 5;
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
        if (bigLength > BigInt(Number.MAX_SAFE_INTEGER)) {
          throw new Error("WebSocket frame is too large");
        }
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

function connectCdp(wsUrl) {
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

async function waitForLibraTab(port, targetUrl) {
  const deadline = Date.now() + 45000;
  let created = false;
  while (Date.now() < deadline) {
    const tabs = await httpJson(port, "/json/list");
    const libraTab = tabs.find((tab) => tab.type === "page" && tab.url && tab.url.includes("/libra/flights"));
    if (libraTab) return libraTab;
    const ssoTab = tabs.find((tab) => tab.type === "page" && /sso\.bytedance\.com|accounts\.google\.com/.test(tab.url || ""));
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
  throw new Error(`Libra tab did not become available. Open pages:\n${visible}`);
}

function firstValue(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return values.length ? values[values.length - 1] : "";
}

function formatTime(value) {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value === "string" && /[^\d.]/.test(value.trim())) return value.trim();
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const ms = numeric > 10_000_000_000 ? numeric : numeric * 1000;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ms));
}

function ownerNames(experiment) {
  const owners = Array.isArray(experiment.owners) ? experiment.owners : [];
  return owners.map((owner) => (typeof owner === "string" ? owner : owner && owner.name)).filter(Boolean);
}

function reversalLabel(reversalType) {
  if (Number(reversalType) === 2) return "反转实验";
  if (Number(reversalType) === 1) return "已开启反转";
  return "普通实验";
}

function detailUrl(experiment, fallbackAppId) {
  const id = firstValue(experiment.id, experiment.flight_id, experiment.experiment_id);
  const appId = firstValue(experiment.app_id, experiment.appId, experiment.app?.id, fallbackAppId, "-1");
  if (!id) return "";
  return `${TARGET_ORIGIN}/datatester/app/${encodeURIComponent(appId)}/experiment/${encodeURIComponent(id)}/detail`;
}

function simplifyExperiment(experiment, fallbackAppId) {
  const statusCode = firstValue(experiment.status, experiment.state, "");
  const reversalType = firstValue(experiment.reversal_type, experiment.reversalType, 0);
  const id = firstValue(experiment.id, experiment.flight_id, experiment.experiment_id, "");
  return {
    id,
    name: firstValue(experiment.name, experiment.title, experiment.flight_name, experiment.experiment_name, ""),
    status: firstValue(experiment.status_name, experiment.status_text, experiment.state_name, STATUS_LABELS[statusCode], String(statusCode)),
    status_code: statusCode,
    owners: ownerNames(experiment),
    creator: firstValue(experiment.creator?.name, experiment.creator_name, ""),
    created_time: formatTime(firstValue(
      experiment.create_time,
      experiment.created_time,
      experiment.created_at,
      experiment.createdAt,
      experiment.createAt,
      experiment.start_time,
      experiment.startTime,
      experiment.start_at,
    )),
    start_time: formatTime(firstValue(experiment.start_time, experiment.startTime, experiment.start_at)),
    end_time: formatTime(firstValue(experiment.end_time, experiment.endTime, experiment.end_at)),
    app_id: firstValue(experiment.app_id, experiment.appId, experiment.app?.id, fallbackAppId, "-1"),
    product_name: firstValue(experiment.product_name, experiment.productName, ""),
    layer_name: firstValue(experiment.layer_name, experiment.layerName, ""),
    reversal_type: reversalType,
    reversal_key: firstValue(experiment.reversal_key, experiment.reversalKey, ""),
    is_reversal: [1, 2].includes(Number(reversalType)),
    reversal_label: reversalLabel(reversalType),
    url: detailUrl(experiment, fallbackAppId),
  };
}

function markdownTable(rows) {
  const lines = ["| 实验 ID | 实验名称 | 状态 | 创建时间 | 实验标签 |", "|---:|---|---|---|---|"];
  for (const row of rows) {
    const name = String(row.name).replaceAll("|", "\\|");
    const link = row.url ? `[${name}](${row.url})` : name;
    lines.push(`| ${row.id} | ${link} | ${row.status} | ${row.created_time || "-"} | ${row.reversal_label || "普通实验"} |`);
  }
  return `${lines.join("\n")}\n`;
}

function isRunningExperiment(row) {
  return Number(row.status_code) === 1 || row.status === "进行中";
}

async function readExperiments(wsUrl, apiPath, args) {
  const cdp = connectCdp(wsUrl);
  await cdp.ready;
  try {
    await cdp.send("Runtime.enable");
    await sleep(5000);
    const expression = `
      fetch(${JSON.stringify(apiPath)}, { credentials: "include" })
        .then(async (response) => {
          const body = await response.json();
          const data = body.data || body;
          const experiments = data.experiments || data.list || data.results || data.items || [];
          return {
            code: body.code,
            message: body.message,
            count: experiments.length,
            rows: experiments,
          };
        })
    `;
    const result = await cdp.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    const value = result.result?.result?.value;
    if (!value) throw new Error("Libra fetch returned no value");
    if (value.code !== 200) throw new Error(`Libra API returned code=${value.code} message=${value.message || ""}`);
    const rawRows = Array.isArray(value.rows) ? value.rows : [];
    const rows = rawRows
      .map((experiment) => simplifyExperiment(experiment, args.appId))
      .filter((row) => !args.runningOnly || isRunningExperiment(row))
      .slice(0, Number(args.limit));
    return {
      code: value.code,
      message: value.message,
      count: value.count,
      rows,
    };
  } finally {
    cdp.close();
  }
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
      "--window-size=1280,900",
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

  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ayla-libra-chrome-"));
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
    const listPath = `/datatester/experiment/api/v3/app/${encodeURIComponent(args.appId)}/experiment?owner_type=${encodeURIComponent(args.ownerType)}&page=${encodeURIComponent(args.page)}&page_size=${encodeURIComponent(args.pageSize)}&search_type=fuzzy`;
    const targetUrl = `${TARGET_ORIGIN}/libra/flights?app_id=${encodeURIComponent(args.appId)}&owner_type=${encodeURIComponent(args.ownerType)}&page=${encodeURIComponent(args.page)}&page_size=${encodeURIComponent(args.pageSize)}&search_type=fuzzy`;

    chrome = spawn(args.chrome, chromeLaunchArgs(args, tempRoot, port, targetUrl), {
      stdio: ["ignore", "ignore", "ignore"],
    });

    const version = await waitForChrome(port);
    browserWsUrl = version.webSocketDebuggerUrl;
    const tab = await waitForLibraTab(port, targetUrl);
    const result = await readExperiments(tab.webSocketDebuggerUrl, listPath, args);

    if (args.json) {
      process.stdout.write(JSON.stringify({ route: "chrome-profile-browser-bridge", visible: args.visible, ...result }, null, 2));
      process.stdout.write("\n");
    } else {
      process.stdout.write(markdownTable(result.rows));
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
