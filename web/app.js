const titleByView = {
  dashboard: "工作台 / 今日看板",
  okr: "工作台 / OKR 看板",
  memos: "工作台 / 备忘看板",
  knowledge: "资产看板",
  settings: "工作台 / 个人设置",
};

const defaultWorkbenchPrefs = {
  uiMode: localStorage.getItem("aylaThemeMode") || localStorage.getItem("aylaTheme") || "light",
  quadHighLabel: "重要且紧急",
  quadFocusLabel: "重要不紧急",
  quadOpsLabel: "紧急不重要",
  quadLowLabel: "不重要不紧急",
  colorHigh: "#ff3b30",
  colorMedium: "#ff9500",
  colorLow: "#34c759",
  defaultWindow: "7",
  okrCycle: "季度",
  syncFrequency: "每 30 分钟",
};

function loadWorkbenchPrefs() {
  try {
    return { ...defaultWorkbenchPrefs, ...JSON.parse(localStorage.getItem("aylaWorkbenchPrefs") || "{}") };
  } catch {
    return { ...defaultWorkbenchPrefs };
  }
}

function systemTheme() {
  return window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "dark" : "light";
}

function themeFromMode(mode) {
  if (mode === "system") return systemTheme();
  return mode === "dark" ? "dark" : "light";
}

const initialWorkbenchPrefs = loadWorkbenchPrefs();

const state = {
  view: "dashboard",
  data: null,
  larkStatus: null,
  larkStatusLoading: false,
  larkBinding: null,
  libraExperiments: null,
  libraExperimentsLoading: false,
  libraExperimentsError: "",
  theme: themeFromMode(initialWorkbenchPrefs.uiMode),
  prefs: initialWorkbenchPrefs,
  sidebarCollapsed: localStorage.getItem("aylaSidebarCollapsed") === "true",
  selectedNoteId: "",
  notificationEnabled: localStorage.getItem("aylaNotificationsEnabled") === "true",
  inboxFilters: {
    status: "all",
    type: "all",
  },
  taskFilter: "active",
  alarmTaskId: "",
  taskMemoryId: "",
  taskEditId: "",
  todoAddQuadrant: "",
  dailyRefreshTimer: 0,
  okrYear: localStorage.getItem("aylaOkrYear") || "2026",
  okrQuarter: localStorage.getItem("aylaOkrQuarter") || "Q2",
  okrWindow: localStorage.getItem("aylaOkrWindow") || "7",
};

const legacyViewMap = {
  agent: "okr",
  graph: "knowledge",
  inbox: "dashboard",
  tasks: "dashboard",
};

const root = document.querySelector("#view-root");
const titleEl = document.querySelector("#view-title");
const statusEl = document.querySelector("#workspace-status");
const toastEl = document.querySelector("#toast");
const notifyButton = document.querySelector('[data-action="toggle-notifications"]');
const themeIcon = document.querySelector("#theme-icon");
const brandAccountEl = document.querySelector("#brand-account");
const userAvatarEl = document.querySelector("#user-avatar");
const userNameEl = document.querySelector("#user-name");
const userHandleEl = document.querySelector("#user-handle");
let toastTimer = 0;

function applyChromeState() {
  state.theme = themeFromMode(state.prefs.uiMode || state.theme);
  document.documentElement.dataset.theme = state.theme === "dark" ? "dark" : "light";
  document.documentElement.style.setProperty("--todo-high", state.prefs.colorHigh || "#ff3b30");
  document.documentElement.style.setProperty("--todo-medium", state.prefs.colorMedium || "#ff9500");
  document.documentElement.style.setProperty("--todo-low", state.prefs.colorLow || "#34c759");
  document.body.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  if (themeIcon) {
    themeIcon.textContent = state.theme === "dark" ? "☀" : "◐";
  }
}

function saveWorkbenchPrefs() {
  localStorage.setItem("aylaWorkbenchPrefs", JSON.stringify(state.prefs));
  localStorage.setItem("aylaThemeMode", state.prefs.uiMode || "light");
  localStorage.setItem("aylaTheme", state.theme);
}

function renderAccountChrome() {
  const profile = state.data?.profile || {};
  const displayName = profile.display_name || "本地用户";
  const handle = profile.handle || "@ayla.local";
  const avatar = profile.avatar || displayName.slice(0, 2) || "AY";
  if (brandAccountEl) {
    brandAccountEl.textContent = profile.bound ? `${displayName} · 已绑定` : `${displayName} · 演示身份`;
  }
  if (userAvatarEl) userAvatarEl.textContent = avatar;
  if (userNameEl) userNameEl.textContent = displayName;
  if (userHandleEl) userHandleEl.textContent = handle;
}

function todayDateKey() {
  return dateKeyOffset(0);
}

function workbenchTodayKey() {
  return state.data?.today || todayDateKey();
}

function dateKeyOffset(daysOffset, baseValue = null) {
  const date = baseValue ? new Date(baseValue) : new Date();
  date.setDate(date.getDate() + daysOffset);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function workbenchDateKeyOffset(daysOffset) {
  return dateKeyOffset(daysOffset, `${workbenchTodayKey()}T00:00:00`);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "未设置";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateOnly(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });
}

function showToast(message, options = {}) {
  const { tone = "", duration = 2200 } = options;
  window.clearTimeout(toastTimer);
  toastEl.textContent = message;
  toastEl.classList.remove("info", "success", "warning");
  if (tone) toastEl.classList.add(tone);
  toastEl.classList.add("show");
  toastTimer = window.setTimeout(() => toastEl.classList.remove("show"), duration);
}

function isModelOrganizeEnabled() {
  return Boolean(state.data?.model_cli_status?.enabled);
}

function setFormBusy(form, busy, label = "处理中") {
  const submit = form.querySelector('button[type="submit"]');
  if (submit) {
    if (!submit.dataset.idleText) submit.dataset.idleText = submit.textContent;
    submit.disabled = busy;
    submit.textContent = busy ? label : submit.dataset.idleText;
  }
  form.classList.toggle("is-submitting", busy);
  form.setAttribute("aria-busy", String(busy));
}

function notificationSupported() {
  return "Notification" in window;
}

function updateNotificationButton() {
  if (!notifyButton) return;
  if (!notificationSupported()) {
    notifyButton.textContent = "提醒不可用";
    notifyButton.disabled = true;
    return;
  }
  if (state.notificationEnabled && Notification.permission === "granted") {
    notifyButton.textContent = "提醒开启";
    notifyButton.classList.add("active");
  } else if (Notification.permission === "denied") {
    notifyButton.textContent = "提醒被拒";
    notifyButton.classList.remove("active");
  } else {
    notifyButton.textContent = "开启提醒";
    notifyButton.classList.remove("active");
  }
}

function notifiedKeys() {
  try {
    return JSON.parse(localStorage.getItem("aylaNotifiedTasks") || "{}");
  } catch {
    return {};
  }
}

function saveNotifiedKeys(keys) {
  localStorage.setItem("aylaNotifiedTasks", JSON.stringify(keys));
}

function checkTaskNotifications() {
  if (!state.data) {
    return;
  }
  const dueTask = (state.data.tasks || []).find(isTaskDueNow);
  if (!dueTask) {
    if (state.alarmTaskId) {
      state.alarmTaskId = "";
      renderAlarmOverlay();
    }
    return;
  }
  if (state.alarmTaskId !== dueTask.id) {
    state.alarmTaskId = dueTask.id;
    renderAlarmOverlay();
  }
  if (!state.notificationEnabled || !notificationSupported() || Notification.permission !== "granted") {
    return;
  }
  const keys = notifiedKeys();
  const key = `${dueTask.id}:${dueTask.due_at}:${dueTask.reminder_snoozed_until || ""}`;
  if (keys[key]) {
    return;
  }
  try {
    new Notification("Ayla TODO 到点了", {
      body: `${formatDeadline(dueTask.due_at)}：${dueTask.title}`,
      tag: key,
    });
  } catch {
    return;
  }
  keys[key] = Date.now();
  saveNotifiedKeys(keys);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

async function loadState() {
  state.data = await api("/api/state");
  scheduleDailyRefresh();
  renderAccountChrome();
  statusEl.textContent = state.data.workspace;
  if (!state.selectedNoteId && state.data.notes.length) {
    state.selectedNoteId = state.data.notes[0].id;
  }
  updateNotificationButton();
  checkTaskNotifications();
}

function scheduleDailyRefresh() {
  if (state.dailyRefreshTimer) {
    window.clearTimeout(state.dailyRefreshTimer);
    state.dailyRefreshTimer = 0;
  }
  const refreshAt = state.data?.next_daily_refresh_at;
  const refreshTime = refreshAt ? new Date(refreshAt).getTime() : Number.NaN;
  if (!Number.isFinite(refreshTime)) return;
  const delay = Math.max(1000, Math.min(refreshTime - Date.now() + 1500, 24 * 60 * 60 * 1000 + 60 * 1000));
  state.dailyRefreshTimer = window.setTimeout(async () => {
    try {
      await refresh("已切换到新的自然日，今日整理已更新", { tone: "info" });
    } catch (error) {
      showToast(error.message);
      scheduleDailyRefresh();
    }
  }, delay);
}

async function refreshIfNaturalDayChanged() {
  if (!state.data?.today || state.data.today === todayDateKey()) return;
  await refresh("已切换到新的自然日，今日整理已更新", { tone: "info" });
}

function normalizeView(view) {
  const normalized = legacyViewMap[view] || view || "dashboard";
  return titleByView[normalized] ? normalized : "dashboard";
}

function setView(view) {
  const nextView = normalizeView(view);
  state.view = nextView;
  window.location.hash = nextView;
  render();
}

function maybeLoadLarkStatus() {
  if (state.view !== "settings" || state.larkStatus || state.larkStatusLoading) return;
  state.larkStatusLoading = true;
  api("/api/connectors/lark/status")
    .then((status) => {
      state.larkStatus = status;
      state.larkStatusLoading = false;
      if (state.view === "settings") render();
    })
    .catch((error) => {
      state.larkStatusLoading = false;
      showToast(error.message);
      if (state.view === "settings") render();
    });
}

function maybeLoadLibraExperiments() {
  if (state.view !== "memos" || state.libraExperiments || state.libraExperimentsLoading || state.libraExperimentsError) return;
  loadLibraExperiments();
}

function loadLibraExperiments(force = false) {
  if (state.libraExperimentsLoading) return Promise.resolve();
  state.libraExperimentsLoading = true;
  state.libraExperimentsError = "";
  const params = new URLSearchParams({
    limit: "50",
    owner_type: "my",
  });
  if (force) params.set("refresh", "1");
  return api(`/api/connectors/libra/experiments?${params.toString()}`)
    .then(async (payload) => {
      state.libraExperiments = payload;
      state.libraExperimentsError = payload.ok ? "" : payload.error || "Libra 连接不可用";
      if (Number(payload.recycle_todos?.created || 0) > 0) {
        await loadState();
      }
      state.libraExperimentsLoading = false;
      if (state.view === "memos") render();
    })
    .catch((error) => {
      state.libraExperimentsLoading = false;
      state.libraExperimentsError = error.message;
      if (state.view === "memos") render();
    });
}

function metaPill(label, tone = "") {
  return `<span class="pill ${tone}">${escapeHtml(label)}</span>`;
}

function settingEnabled(value) {
  return value === true || String(value ?? "").toLowerCase() === "true";
}

function statusTone(status) {
  if (["待确认", "候选", "待办", "进行中", "自动分类"].includes(status)) return "green";
  if (["需补充"].includes(status)) return "amber";
  if (["已忽略", "已取消"].includes(status)) return "coral";
  if (["已确认", "已完成", "已归档", "已发布"].includes(status)) return "violet";
  return "";
}

function typeLabel(type) {
  const labels = {
    memo: "备忘",
    task_candidate: "TODO 候选",
    summary: "摘要",
    note_candidate: "笔记候选",
    knowledge_candidate: "知识库候选",
    memory_candidate: "Agent 记忆候选",
    pinned_candidate: "固定便笺候选",
    work_record_candidate: "工作沉淀候选",
    report_material_candidate: "总结素材候选",
  };
  return labels[type] || type;
}

function targetLabel(target) {
  const labels = {
    todo: "自动归为 TODO",
    note: "自动归为知识",
    memory: "进入 AgentMemory",
    pinned: "自动归为便笺",
    memo: "仅归档备忘",
  };
  return labels[target] || target;
}

function storageTargetLabel(target) {
  const labels = {
    local_state: "本地工作库",
    feishu_doc: "飞书文档草稿",
    obsidian_public_vault: "公开知识 Vault",
  };
  return labels[target] || target || "本地工作库";
}

function visibilityLabel(visibility) {
  const labels = {
    public: "公开",
    internal: "内部",
    private: "私有",
  };
  return labels[visibility] || visibility || "私有";
}

function policyLabel(policy) {
  const labels = {
    auto_read: "自动读",
    auto_draft: "自动候选",
    batch_confirm: "批量确认",
    instant_confirm: "即时确认",
    double_confirm: "二次确认",
    forbidden: "禁止自动",
  };
  return labels[policy] || policy || "批量确认";
}

function confidence(item) {
  return `${Math.round((Number(item.confidence) || 0) * 100)}%`;
}

function sourceLink(url) {
  if (!url) return "";
  return `<a class="source-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">来源链接</a>`;
}

function isTodayRecord(item) {
  return isTodayValue(item.updated_at) || isTodayValue(item.created_at) || isTodayValue(item.collected_at);
}

function notePreview(note) {
  const content = String(note.content || "").replace(/^---[\s\S]*?---\s*/, "").trim();
  return content || note.path || "已写入本地资料库";
}

function summaryPreview(value, limit = 180) {
  const text = String(value || "")
    .replace(/^---[\s\S]*?---\s*/, "")
    .replace(/^# .+$/m, "")
    .replace(/\n来源：https?:\/\/\S+/g, "")
    .trim();
  return text.length > limit ? `${text.slice(0, limit).trim()}...` : text;
}

function isActiveTask(task) {
  return !["已完成", "已取消", "已归档"].includes(task.status);
}

function isTodayValue(value) {
  return Boolean(value && String(value).slice(0, 10) === workbenchTodayKey());
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function datetimeLocalValue(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function defaultTaskDeadlineValue() {
  const date = new Date();
  date.setMinutes(date.getMinutes() + 60);
  date.setSeconds(0, 0);
  return datetimeLocalValue(date);
}

function datetimeAfterMinutes(minutes) {
  const date = new Date();
  date.setMinutes(date.getMinutes() + Number(minutes || 10));
  date.setSeconds(0, 0);
  return datetimeLocalValue(date);
}

function parseTaskDeadline(value) {
  if (!value) return null;
  const raw = String(value);
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T18:00` : raw.replace(" ", "T");
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function toDatetimeInputValue(value) {
  const date = parseTaskDeadline(value);
  return date ? datetimeLocalValue(date) : defaultTaskDeadlineValue();
}

function formatDeadline(value) {
  const date = parseTaskDeadline(value);
  if (!date) return "未设 DDL";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function minutesUntilDeadline(task) {
  const date = parseTaskDeadline(task.due_at);
  if (!date) return Number.POSITIVE_INFINITY;
  return Math.round((date.getTime() - Date.now()) / 60000);
}

function snoozedUntil(task) {
  return parseTaskDeadline(task.reminder_snoozed_until);
}

function isSnoozed(task) {
  const snoozed = snoozedUntil(task);
  return Boolean(snoozed && snoozed.getTime() > Date.now());
}

function isTaskDueNow(task) {
  const due = parseTaskDeadline(task.due_at);
  if (!due || !isActiveTask(task)) return false;
  if (isSnoozed(task)) return false;
  return due.getTime() <= Date.now();
}

function taskRuntimeLabel(task) {
  if (task.status === "已完成") return "已完成";
  if (!parseTaskDeadline(task.due_at)) return "待补 DDL";
  if (isSnoozed(task)) return `延时到 ${formatDeadline(task.reminder_snoozed_until)}`;
  const minutes = minutesUntilDeadline(task);
  if (minutes < 0) return `已超时 ${Math.abs(minutes)} 分钟`;
  if (minutes === 0) return "即将提醒";
  return `${minutes} 分钟后提醒`;
}

function taskRuntimeTone(task) {
  if (task.status === "已完成") return "violet";
  if (!parseTaskDeadline(task.due_at)) return "amber";
  if (isSnoozed(task)) return "amber";
  if (minutesUntilDeadline(task) <= 0) return "coral";
  return "green";
}

function todayRelevantTasks() {
  return (state.data.tasks || [])
    .filter((task) => isActiveTask(task) || isTodayValue(task.due_at) || isTodayValue(task.completed_at) || isTodayValue(task.updated_at))
    .slice(0, 24);
}

function renderQuickMemo(variant = "default") {
  return `
    <form class="memo-form" data-form="memo">
      <div class="memo-mode" role="radiogroup" aria-label="记录模式">
        <label>
          <input type="radio" name="mode" value="auto" checked />
          <span>自动整理</span>
        </label>
        <label>
          <input type="radio" name="mode" value="pinned" />
          <span>固定便笺</span>
        </label>
      </div>
      <div class="form-row">
        <label for="memo-content-${variant}">备忘内容</label>
        <textarea class="textarea" id="memo-content-${variant}" name="content" placeholder="写下刚刚发生的事、需要跟进的任务或值得沉淀的资料。默认进入今日整理；切到固定便笺时会直接新增一张长期便笺。"></textarea>
      </div>
      <div class="form-row pinned-memo-fields" hidden>
        <label for="memo-title-${variant}">便笺标题</label>
        <input class="input" id="memo-title-${variant}" name="title" placeholder="不填则用第一行内容作为标题" />
      </div>
      <div class="form-row">
        <label for="memo-partition-${variant}">分区</label>
        <select class="select" id="memo-partition-${variant}" name="partition">
          <option value="">自动判断</option>
          <option value="工作">工作</option>
          <option value="学习">学习</option>
          <option value="项目">项目</option>
          <option value="个人">个人</option>
          <option value="待整理">待整理</option>
        </select>
      </div>
      <div class="form-actions">
        <button class="button" type="submit">记录</button>
      </div>
    </form>
  `;
}

function setMemoMode(form, mode) {
  const isPinned = mode === "pinned";
  const titleField = form.querySelector(".pinned-memo-fields");
  const submit = form.querySelector('button[type="submit"]');
  if (titleField) {
    titleField.hidden = !isPinned;
  }
  if (submit) {
    submit.textContent = isPinned ? "新增固定便笺" : "记录";
  }
}

function renderStats() {
  const stats = state.data.stats;
  return `
    <div class="stats-grid">
      <div class="stat">
        <strong>${stats.pending_inbox}</strong>
        <span>待整理</span>
      </div>
      <div class="stat">
        <strong>${stats.today_tasks}</strong>
        <span>未完成 TODO</span>
      </div>
      <div class="stat">
        <strong>${stats.notes}</strong>
        <span>知识笔记</span>
      </div>
      <div class="stat">
        <strong>${stats.agent_memories || 0}</strong>
        <span>Agent 记忆</span>
      </div>
    </div>
  `;
}

function weekdayLabel(date = new Date()) {
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][date.getDay()];
}

function todayDisplayLabel() {
  const date = new Date(`${workbenchTodayKey()}T00:00:00`);
  return `${date.getMonth() + 1}月${date.getDate()}日 ${weekdayLabel(date)}`;
}

function larkConnectorPill() {
  const settings = state.data.settings || {};
  const feishuEnabled = settingEnabled(settings.feishu_enabled);
  if (state.larkStatus?.auth?.ok && state.larkStatus?.scope_check?.ok) {
    return metaPill(`飞书 ${state.larkStatus.auth.user_name || "已连接"}`, "green");
  }
  return metaPill(feishuEnabled ? "飞书待检查" : "飞书未启用", feishuEnabled ? "amber" : "");
}

function qrImageSrc(value) {
  if (!value) return "";
  return `https://api.qrserver.com/v1/create-qr-code/?size=176x176&margin=8&data=${encodeURIComponent(value)}`;
}

function renderPermissionChecklist(larkStatus, profile) {
  const permissions = larkStatus?.permission_summary || [
    { label: "飞书 CLI 授权认证", ok: false, detail: "尚未检查" },
    { label: "日历 / 妙记基础只读权限", ok: false, detail: "尚未检查" },
    { label: "妙记全文与 TODO 抽取权限", ok: false, detail: "尚未检查" },
  ];
  const rows = [
    ...permissions,
    {
      label: "资料归属写入",
      ok: !!profile?.bound,
      detail: profile?.bound ? `${profile.display_name || "授权人"} · ${profile.provider || ""}` : "当前仍是本地演示身份",
    },
  ];
  return `
    <div class="permission-list">
      ${rows.map((item) => `
        <div class="permission-row ${item.ok ? "ok" : "pending"}">
          <span class="permission-dot">${item.ok ? "✓" : "!"}</span>
          <div>
            <strong>${escapeHtml(item.label)}</strong>
            <small>${escapeHtml(item.detail || (item.ok ? "已完成" : "待处理"))}</small>
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderLarkBindingSession(session) {
  if (!session) return "";
  const qrTarget = session.verification_url || session.complete_url || "";
  const qrSrc = qrImageSrc(qrTarget);
  return `
    <div class="qr-bind-panel">
      <div class="qr-box">
        ${qrSrc ? `<img src="${escapeHtml(qrSrc)}" alt="飞书扫码授权二维码" />` : `<span>等待授权链接</span>`}
      </div>
      <div class="qr-copy">
        <strong>扫码打开飞书授权页</strong>
        <p>扫码后在页面输入验证码，完成后回到这里点「完成绑定」。</p>
        ${session.user_code ? `<div class="auth-code">${escapeHtml(session.user_code)}</div>` : ""}
        ${qrTarget ? `<a href="${escapeHtml(qrTarget)}" target="_blank" rel="noreferrer">打开授权页</a>` : ""}
      </div>
    </div>
  `;
}

function latestEventsByType(type, limit = 3) {
  return (state.data.events || []).filter((event) => event.source_type === type && isTodayRecord(event)).slice(0, limit);
}

function renderDailyBriefCard() {
  const review = state.data.daily_review || {};
  const archive = state.data.daily_archive || { counts: {} };
  const calendarEvents = latestEventsByType("lark_calendar", 3);
  const minutesEvents = latestEventsByType("lark_minutes", 2);
  const activeTasks = todayRelevantTasks().filter(isActiveTask);
  const todayPending = archive.counts?.adjustable || review.today_count || 0;
  return `
    <article class="card span-5 daily-brief-card">
      <div class="card-header">
        <div class="card-title">
          <h3>今日信息卡</h3>
          <span>日期 / 飞书 / 输入 / TODO</span>
        </div>
        <span class="pill">Daily Brief</span>
      </div>
      <div class="dashboard-overview">
        <section class="overview-block date-display">
          <h4>今日日期</h4>
          <div class="date-main">${todayDisplayLabel()}</div>
          <div class="date-sub">${workbenchTodayKey()} · 本地时间</div>
        </section>
        <section class="overview-block">
          <h4>数据源</h4>
          <div class="meta-line">
            ${larkConnectorPill()}
            ${metaPill(state.data.model_cli_status?.enabled ? "模型整理" : "本地规则", state.data.model_cli_status?.enabled ? "green" : "")}
          </div>
        </section>
        <section class="overview-block">
          <h4>今日信号</h4>
          <div class="metric-grid mini">
            <div><strong>${todayPending}</strong><span>待整理</span></div>
            <div><strong>${activeTasks.length}</strong><span>未完成</span></div>
            <div><strong>${archive.counts?.events || 0}</strong><span>输入</span></div>
            <div><strong>${review.today_count || 0}</strong><span>候选</span></div>
          </div>
        </section>
        <section class="overview-block">
          <h4>飞书日程</h4>
          <div class="brief-list">
            ${calendarEvents.length ? calendarEvents.map((event) => `
              <article class="brief-row">
                <strong>${escapeHtml(event.title || "飞书日程")}</strong>
                <span>${formatDate(event.collected_at)}</span>
              </article>
            `).join("") : `<div class="empty-hint">同步后展示今日日程</div>`}
          </div>
        </section>
        <section class="overview-block">
          <h4>妙记线索</h4>
          <div class="brief-list">
            ${minutesEvents.length ? minutesEvents.map((event) => `
              <article class="brief-row">
                <strong>${escapeHtml(event.title || "飞书妙记")}</strong>
                <span>${formatDate(event.collected_at)}</span>
              </article>
            `).join("") : `<div class="empty-hint">同步后展示妙记记录</div>`}
          </div>
        </section>
      </div>
    </article>
  `;
}

function renderAiSummaryCard() {
  const log = state.data.today_work_log || {};
  const archive = state.data.daily_archive || { adjustable: [] };
  const pendingInbox = (state.data.inbox || []).filter((item) => isTodayRecord(item) && !["已确认", "已忽略", "已归档", "已发布"].includes(item.status));
  const recentRuns = (state.data.agent_runs || []).filter(isTodayRecord);
  const lead = log.summary || log.generated_report || "今天的输入会被整理成候选、TODO 和本地工作沉淀，等待你确认。";
  const knownTodos = todayRelevantTasks().filter(isActiveTask).slice(0, 2);
  const linkSummaries = (state.data.link_summaries || []).filter(isTodayRecord).slice(0, 3);
  return `
    <article class="card span-7 ai-summary-card">
      <div class="card-header">
        <div class="card-title">
          <h3>AI 智能总结</h3>
          <span>聚合今日输入、AgentRun、TODO 与飞书线索</span>
        </div>
        <span class="pill green">Ayla Daily</span>
      </div>
      <div class="ai-summary-shell">
        <section class="ai-summary-highlight">${escapeHtml(lead)}</section>
        <section class="ai-summary-overview">
          <article class="ai-summary-item">
            <header><strong>会议与输入</strong>${metaPill(`${archive.counts?.events || 0} 条输入`, "green")}</header>
            <p>${escapeHtml((archive.events || []).slice(0, 3).map((event) => event.title || event.source_type).join(" / ") || "暂无今日输入")}</p>
          </article>
          <article class="ai-summary-item">
            <header><strong>AgentRun</strong>${metaPill(`${recentRuns.length} 次`, "violet")}</header>
            <p>${escapeHtml(recentRuns[0]?.candidate_output?.summary || "暂无新的 AgentRun 摘要")}</p>
          </article>
          <article class="ai-summary-item">
            <header><strong>确认队列</strong>${metaPill(`${pendingInbox.length} 条`, pendingInbox.length ? "amber" : "green")}</header>
            <p>${escapeHtml(pendingInbox.slice(0, 3).map((item) => item.title).join(" / ") || "确认队列清空")}</p>
          </article>
        </section>
        <section class="ai-zone">
          <div class="ai-zone-head">
            <div><h4>链接总结</h4><span>飞书文档 / 网页资料</span></div>
            <span class="pill">Link Brief</span>
          </div>
          <div class="ai-action-list">
            ${linkSummaries.length ? linkSummaries.map((item) => `
              <article class="ai-action-item ${item.failed ? "is-warning" : ""}">
                <div class="ai-action-main">
                  <div>
                    <strong>${escapeHtml(item.title)}</strong>
                    <p>${escapeHtml(summaryPreview(item.summary, 220))}</p>
                    ${item.failed ? `<p class="link-fetch-warning">抓取失败：${escapeHtml(item.fetch_error || "内容暂未完整抓取")}</p>` : ""}
                  </div>
                  <div class="link-summary-actions">
                    ${sourceLink(item.source_url)}
                    <button class="button tiny warning" data-action="remove-link-summary" data-id="${escapeHtml(item.id)}" type="button">无用</button>
                  </div>
                </div>
                <div class="meta-line">${metaPill(item.failed ? "抓取失败" : item.provider_label || "链接", item.failed ? "coral" : item.provider === "lark-cli-docs" ? "violet" : "green")}${metaPill(formatDate(item.updated_at))}</div>
              </article>
            `).join("") : `<div class="empty-hint">暂无链接总结</div>`}
          </div>
        </section>
        <section class="ai-zone">
          <div class="ai-zone-head">
            <div><h4>AI 已知 TODO</h4><span>已识别为任务的内容会进入今日 TODO，可按需移除。</span></div>
            <span class="pill">Task Copilot</span>
          </div>
          <div class="ai-action-list">
            ${knownTodos.length ? knownTodos.map((item) => `
              <article class="ai-action-item">
                <div class="ai-action-main">
                  <div>
                    <strong>${escapeHtml(item.title)}</strong>
                    <p>${escapeHtml(taskDescription(item))}</p>
                  </div>
                  <button class="button" data-action="remove-ai-todo" data-id="${escapeHtml(item.id)}" type="button">移除 TODO</button>
                </div>
                <div class="meta-line">${metaPill(item.status, statusTone(item.status))}${metaPill(taskRuntimeLabel(item), taskRuntimeTone(item))}</div>
              </article>
            `).join("") : `<div class="empty-hint">暂无已知 TODO</div>`}
          </div>
        </section>
      </div>
    </article>
  `;
}

function taskQuadrant(task) {
  const minutes = minutesUntilDeadline(task);
  if (task.priority === "high" || minutes <= 24 * 60) return "high";
  if (task.priority === "medium" || task.project_id) return "focus";
  if (minutes <= 3 * 24 * 60) return "ops";
  return "low";
}

const defaultTodoQuadrants = {
  high: {
    label: "重要且紧急",
    priority: "high",
    hint: "可以直接点击归档，调用 AI 智能总结存档，无意义的事情不需要记录",
  },
  focus: {
    label: "重要不紧急",
    priority: "medium",
    hint: "",
  },
  ops: {
    label: "紧急不重要",
    priority: "normal",
    hint: "",
  },
  low: {
    label: "不重要不紧急",
    priority: "low",
    hint: "",
  },
};

function getTodoQuadrants() {
  return {
    high: { ...defaultTodoQuadrants.high, label: state.prefs.quadHighLabel || defaultTodoQuadrants.high.label },
    focus: { ...defaultTodoQuadrants.focus, label: state.prefs.quadFocusLabel || defaultTodoQuadrants.focus.label },
    ops: { ...defaultTodoQuadrants.ops, label: state.prefs.quadOpsLabel || defaultTodoQuadrants.ops.label },
    low: { ...defaultTodoQuadrants.low, label: state.prefs.quadLowLabel || defaultTodoQuadrants.low.label },
  };
}

function taskDeadlineDate(task) {
  const date = parseTaskDeadline(task.due_at);
  if (!date) return "未设置";
  return `${pad2(date.getMonth() + 1)}/${pad2(date.getDate())}`;
}

function taskDescription(task) {
  return task.description || task.source_title || "仅做本地输入";
}

function taskSourceUrl(task) {
  if (task.source_url) return task.source_url;
  const match = String(task.description || "").match(/链接：\s*(https?:\/\/\S+)/);
  return match ? match[1] : "";
}

function renderTodoQuadrants() {
  const quadrants = getTodoQuadrants();
  const grouped = { high: [], focus: [], ops: [], low: [] };
  todayRelevantTasks().filter(isActiveTask).forEach((task) => grouped[taskQuadrant(task)].push(task));
  return `
    <article class="card span-12 quadrant-panel todo-matrix-panel">
      <div class="todo-matrix-header">
        <div class="card-title">
          <h3>今日 TODO（四象限）</h3>
          <span>点击卡片可跳转来源，勾选圆圈标记完成，支持本地记忆</span>
        </div>
        <span class="todo-matrix-badge"><span class="badge-check">✓</span>Priority Matrix</span>
      </div>
      <div class="quadrant-grid todo-matrix-grid">
        ${Object.entries(quadrants).map(([key, config]) => `
          <section class="quadrant todo-quadrant" data-tone="${key}">
            <div class="quadrant-head">
              <div class="quadrant-title-line">
                <h4>${config.label}</h4>
                ${config.hint ? `<span class="quadrant-hint">${config.hint}</span>` : ""}
              </div>
              <button class="add-task-button" data-action="open-quadrant-add" data-quadrant="${key}" type="button">+ 添加任务</button>
            </div>
            ${state.todoAddQuadrant === key ? renderQuadrantAddForm(key) : ""}
            <div class="todo-list">
              ${grouped[key].length ? grouped[key].map(renderTodoCompact).join("") : `<div class="empty-hint">暂无任务</div>`}
            </div>
          </section>
        `).join("")}
      </div>
    </article>
  `;
}

function renderWorkspaceHero() {
  const stats = state.data.stats;
  const archive = state.data.daily_archive || { counts: {} };
  const review = state.data.daily_review || {};
  const modelStatus = state.data.model_cli_status || {};
  return `
    <section class="workspace-hero">
      <div>
        <div class="hero-kicker">Ayla / Personal Agent</div>
        <h2>今日工作台</h2>
        <p>只保留当天有效的工作信号：总结、TODO、备忘归档、待调整入口。</p>
        <div class="hero-meta">
          ${metaPill(workbenchTodayKey(), "violet")}
          ${metaPill(`${stats.today_tasks || 0} 个未完成 TODO`, "green")}
          ${metaPill(`${archive.counts?.events || 0} 条今日备忘`)}
          ${metaPill(`${review.today_count || 0} 条今日待整理`, review.today_count ? "amber" : "")}
          ${metaPill(modelStatus.enabled ? "模型整理" : "本地规则", modelStatus.enabled ? "green" : "")}
        </div>
      </div>
      <div class="hero-actions">
        <button class="button secondary" data-view="memos" type="button">整理备忘</button>
        <button class="button secondary" data-view="okr" type="button">查看 OKR</button>
      </div>
    </section>
  `;
}

function renderDailyReview(compact = false) {
  const review = state.data.daily_review || { today: "", items: [], today_count: 0, pending_count: 0 };
  const items = review.items || [];
  const rows = compact ? items.slice(0, 3) : items;
  return `
    <section class="panel" id="daily-review-panel">
      <div class="section-head">
        <div>
          <h2>今日增量整理</h2>
          <p>${escapeHtml(review.today)}，${review.today_count || 0} 条今日自动分类，${review.pending_count || 0} 条待整理总量</p>
        </div>
        <button class="button" data-action="confirm-daily-review" data-date="${escapeHtml(review.today)}" ${items.length ? "" : "disabled"} type="button">确认今日整理</button>
      </div>
      <div class="list">
        ${rows.length ? rows.map((item) => renderReviewItem(item, compact)).join("") : `<div class="empty">今天还没有待整理增量</div>`}
      </div>
    </section>
  `;
}

function renderReviewItem(item, compact = false) {
  const metadata = item.metadata || {};
  if (compact) {
    return `
      <article class="item-row compact">
        <div class="meta-line">
          ${metaPill(targetLabel(item.auto_target), "green")}
          ${metaPill(item.suggested_category || "待整理")}
          ${metadata.parser_status === "agent" ? metaPill("OpenClaw", "violet") : ""}
          ${metadata.parser_status === "parsed" ? metaPill("已解析链接", "violet") : ""}
          ${metadata.parser_status === "failed" ? metaPill("链接待解析", "amber") : ""}
          ${metadata.project ? metaPill(metadata.project, "violet") : ""}
          ${metadata.storage_target ? metaPill(storageTargetLabel(metadata.storage_target), "violet") : ""}
          ${metadata.confirmation_policy ? metaPill(policyLabel(metadata.confirmation_policy), metadata.confirmation_policy === "double_confirm" ? "coral" : "amber") : ""}
          <span>置信度 ${confidence(item)}</span>
          ${sourceLink(metadata.source_url)}
        </div>
        <h3>${escapeHtml(item.title)}</h3>
        <p class="content-preview">${escapeHtml(item.content)}</p>
      </article>
    `;
  }
  const tags = Array.isArray(metadata.tags) ? metadata.tags.join(", ") : "";
  return `
    <form class="review-card" data-review-row="${escapeHtml(item.id)}">
      <div class="review-grid">
        <div class="form-row">
          <label>整理到</label>
          <select class="select" name="target">
            ${["todo", "note", "pinned", "memo"].map((target) => `<option value="${target}" ${target === item.auto_target ? "selected" : ""}>${targetLabel(target)}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>分类</label>
          <select class="select" name="category">
            ${["工作", "学习", "项目", "个人", "待整理", "可公开"].map((category) => `<option value="${category}" ${category === item.suggested_category ? "selected" : ""}>${category}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>优先级</label>
          <select class="select" name="priority">
            ${["high", "medium", "normal", "low"].map((priority) => `<option value="${priority}" ${priority === (metadata.suggested_priority || "normal") ? "selected" : ""}>${priority}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>截止时间</label>
          <input class="input" name="due_at" type="date" value="${escapeHtml(metadata.suggested_due_at || "")}" />
        </div>
      </div>
      <div class="form-row">
        <label>标题</label>
        <input class="input" name="title" value="${escapeHtml(item.title)}" />
      </div>
      <div class="form-row">
        <label>内容</label>
        <textarea class="textarea review-textarea" name="content">${escapeHtml(item.content)}</textarea>
      </div>
      <div class="review-grid secondary">
        <div class="form-row">
          <label>项目</label>
          <input class="input" name="project_id" value="${escapeHtml(metadata.project || "")}" />
        </div>
        <div class="form-row">
          <label>落库目标</label>
          <select class="select" name="storage_target">
            ${["local_state", "feishu_doc", "obsidian_public_vault"].map((target) => `<option value="${target}" ${target === (metadata.storage_target || "local_state") ? "selected" : ""}>${storageTargetLabel(target)}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>可见性</label>
          <select class="select" name="visibility">
            ${["private", "internal", "public"].map((visibility) => `<option value="${visibility}" ${visibility === (metadata.visibility || "private") ? "selected" : ""}>${visibilityLabel(visibility)}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>标签</label>
          <input class="input" name="tags" value="${escapeHtml(tags)}" placeholder="逗号分隔" />
        </div>
      </div>
      <div class="meta-line">
        <span>置信度 ${confidence(item)}</span>
        ${metadata.risk_level ? metaPill(`风险 ${metadata.risk_level}`, metadata.risk_level === "high" ? "coral" : metadata.risk_level === "medium" ? "amber" : "green") : ""}
        ${metadata.confirmation_policy ? metaPill(policyLabel(metadata.confirmation_policy), metadata.confirmation_policy === "double_confirm" ? "coral" : "amber") : ""}
        ${metadata.parser_status === "agent" ? metaPill("OpenClaw", "violet") : ""}
        ${metadata.parser_status === "parsed" ? metaPill("已解析链接", "violet") : ""}
        ${metadata.parser_status === "failed" ? metaPill("链接待解析", "amber") : ""}
        ${sourceLink(metadata.source_url)}
        <span>${formatDate(item.created_at)}</span>
      </div>
    </form>
  `;
}

function collectDailyReviewOverrides() {
  return [...document.querySelectorAll("[data-review-row]")].map((form) => ({
    id: form.dataset.reviewRow,
    ...Object.fromEntries(new FormData(form).entries()),
  }));
}

function renderPinnedSlots() {
  const slots = state.data.pinned_slots || [];
  return `
    <section class="panel notes-panel">
      <div class="section-head">
        <div>
          <h2>固定便笺</h2>
          <p>长期固定的信息槽位。也可以在“记录备忘”里切到固定便笺来新增。</p>
        </div>
        <button class="button secondary" data-action="add-pinned-slot" type="button">新建便笺</button>
      </div>
      <div class="pinned-grid">
        ${slots.map((slot) => `
          <form class="slot-card apple-note" data-form="pinned-slot" data-id="${escapeHtml(slot.id)}">
            <div class="slot-card-head">
              <div>
                <input class="slot-title" name="title" value="${escapeHtml(slot.title)}" aria-label="便笺标题" />
                <div class="slot-date">${formatDate(slot.updated_at)}</div>
              </div>
              <select class="slot-category" name="category" aria-label="槽位分区">
                ${["工作", "学习", "项目", "个人", "待整理"].map((item) => `<option value="${item}" ${item === slot.category ? "selected" : ""}>${item}</option>`).join("")}
              </select>
            </div>
            <textarea class="slot-textarea" name="content" aria-label="${escapeHtml(slot.title)}内容">${escapeHtml(slot.content)}</textarea>
            <div class="note-card-actions">
              <button class="button secondary" type="submit">保存</button>
              <button class="button warning" data-action="delete-pinned-slot" data-id="${escapeHtml(slot.id)}" type="button">删除</button>
            </div>
          </form>
        `).join("")}
      </div>
    </section>
  `;
}

function renderInboxItem(item) {
  const metadata = item.metadata || {};
  const project = metadata.project ? metaPill(metadata.project, "violet") : "";
  const risk = metadata.risk ? metaPill("风险", "coral") : "";
  const due = metadata.suggested_due_at ? metaPill(`截止 ${formatDateOnly(metadata.suggested_due_at)}`, "amber") : "";
  const target = metadata.auto_target ? metaPill(targetLabel(metadata.auto_target), "green") : "";
  const parser = metadata.parser_status === "agent" ? metaPill("OpenClaw", "violet") : metadata.parser_status === "parsed" ? metaPill("已解析链接", "violet") : metadata.parser_status === "failed" ? metaPill("链接待解析", "amber") : "";
  const storage = metadata.storage_target ? metaPill(storageTargetLabel(metadata.storage_target), metadata.storage_target === "obsidian_public_vault" ? "violet" : "") : "";
  const visibility = metadata.visibility ? metaPill(visibilityLabel(metadata.visibility), metadata.visibility === "public" ? "violet" : "") : "";
  const policy = metadata.confirmation_policy ? metaPill(policyLabel(metadata.confirmation_policy), metadata.confirmation_policy === "double_confirm" ? "coral" : metadata.confirmation_policy === "instant_confirm" ? "amber" : "green") : "";
  const canAct = !["已确认", "已忽略", "已归档", "已发布"].includes(item.status);
  const confirmNoteText = metadata.storage_target === "obsidian_public_vault" ? "入公开知识" : metadata.candidate_type === "report_material" ? "入总结素材" : "确认落库";
  const isMemoryCandidate = item.item_type === "memory_candidate" || metadata.auto_target === "memory";
  const primaryActions = isMemoryCandidate
    ? `<button class="button" data-action="confirm-memory" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">入 AgentMemory</button>`
    : `
        <button class="button" data-action="confirm-task" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">转 TODO</button>
        <button class="button secondary" data-action="confirm-note" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">${confirmNoteText}</button>
      `;
  return `
    <article class="item-row">
      <div class="meta-line">
        ${metaPill(typeLabel(item.item_type), "green")}
        ${metaPill(item.status, statusTone(item.status))}
        ${metaPill(item.suggested_category || "待整理")}
        ${target}
        ${storage}
        ${visibility}
        ${policy}
        ${project}
        ${risk}
        ${due}
        ${parser}
        <span>置信度 ${confidence(item)}</span>
        ${sourceLink(metadata.source_url)}
        <span>${formatDate(item.updated_at)}</span>
      </div>
      <div>
        <h3>${escapeHtml(item.title)}</h3>
        <p class="content-preview">${escapeHtml(item.content)}</p>
      </div>
      <div class="row-actions">
        ${primaryActions}
        <button class="button secondary" data-action="need-info" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">需补充</button>
        <button class="button warning" data-action="ignore" data-id="${escapeHtml(item.id)}" ${canAct ? "" : "disabled"} type="button">忽略</button>
      </div>
    </article>
  `;
}

function renderTask(task) {
  return `
    <article class="task-row" data-task-row="${escapeHtml(task.id)}">
      <div class="meta-line">
        ${metaPill(task.status, statusTone(task.status))}
        ${metaPill(task.priority || "normal")}
        ${task.due_at ? metaPill(`截止 ${formatDateOnly(task.due_at)}`, "amber") : ""}
        ${task.project_id ? metaPill(task.project_id, "violet") : ""}
        <span>来源 ${escapeHtml(task.source_title || "本地")}</span>
      </div>
      <div class="task-editor">
        <div class="form-row">
          <label>标题</label>
          <input class="input" name="title" value="${escapeHtml(task.title)}" />
        </div>
        <div class="form-row">
          <label>状态</label>
          <select class="select" name="status">
            ${["待办", "进行中", "已完成", "已取消", "已归档"].map((item) => `<option value="${item}" ${item === task.status ? "selected" : ""}>${item}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>优先级</label>
          <select class="select" name="priority">
            ${["high", "medium", "normal", "low"].map((item) => `<option value="${item}" ${item === task.priority ? "selected" : ""}>${item}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>截止时间</label>
          <input class="input" name="due_at" type="date" value="${escapeHtml(task.due_at || "")}" />
        </div>
        <div class="form-row">
          <label>项目</label>
          <input class="input" name="project_id" value="${escapeHtml(task.project_id || "")}" />
        </div>
        <button class="button secondary" data-action="save-task" data-id="${escapeHtml(task.id)}" type="button">保存</button>
      </div>
      <p class="content-preview">${escapeHtml(task.description || "")}</p>
    </article>
  `;
}

function renderTodoCompact(task) {
  const memoryOpen = state.taskMemoryId === task.id;
  const completed = task.status === "已完成";
  const editing = state.taskEditId === task.id;
  const sourceUrl = taskSourceUrl(task);
  const cardActionAttrs = sourceUrl && !memoryOpen
    ? `data-action="open-task-source" data-url="${escapeHtml(sourceUrl)}"`
    : "";
  const checkActionAttrs = completed ? "" : `data-action="complete-task" data-id="${escapeHtml(task.id)}"`;
  if (editing) {
    return `
      <article class="todo-card editing" data-task-row="${escapeHtml(task.id)}">
        <div class="todo-card-edit-grid">
          <input class="input" name="title" value="${escapeHtml(task.title)}" aria-label="TODO 标题" />
          <select class="select" name="priority" aria-label="TODO 优先级">
            ${["high", "medium", "normal", "low"].map((item) => `<option value="${item}" ${item === task.priority ? "selected" : ""}>${item}</option>`).join("")}
          </select>
          <input class="input" name="due_at" type="datetime-local" value="${escapeHtml(toDatetimeInputValue(task.due_at))}" aria-label="DDL" />
          <textarea class="textarea" name="description" aria-label="TODO 说明">${escapeHtml(task.description || "")}</textarea>
        </div>
        <div class="todo-card-actions edit-actions">
          <button class="todo-action primary" data-action="save-task" data-id="${escapeHtml(task.id)}" type="button">保存</button>
          <button class="todo-action" data-action="cancel-task-edit" type="button">取消</button>
        </div>
      </article>
    `;
  }
  return `
    <article class="todo-card ${completed ? "completed" : ""} ${sourceUrl ? "has-source" : "no-source"}" data-task-row="${escapeHtml(task.id)}" ${cardActionAttrs}>
      <label class="task-check" title="标记完成" ${checkActionAttrs}>
        <input type="checkbox" data-action="complete-task" data-id="${escapeHtml(task.id)}" ${completed ? "checked disabled" : ""} />
        <span></span>
      </label>
      <div class="todo-card-main">
        <div class="todo-card-title">${escapeHtml(task.title)}</div>
        <div class="todo-card-deadline">截止： ${taskDeadlineDate(task)}</div>
        <p>${escapeHtml(taskDescription(task))}</p>
      </div>
      <div class="todo-card-actions">
        <button class="todo-action" data-action="edit-task" data-id="${escapeHtml(task.id)}" type="button">编辑</button>
        <button class="todo-action" data-action="delete-task" data-id="${escapeHtml(task.id)}" type="button">删除</button>
      </div>
      ${memoryOpen ? `
        <form class="task-memory-form" data-form="task-memory" data-id="${escapeHtml(task.id)}">
          <textarea class="textarea" name="completion_note" placeholder="记录具体完成了什么、产出在哪里、后续有什么可复用经验。">${escapeHtml(task.completion_note || "")}</textarea>
          <div class="form-actions">
            <button class="button" type="submit">完成并沉淀</button>
            <button class="button secondary" data-action="close-task-memory" type="button">收起</button>
          </div>
        </form>
      ` : ""}
    </article>
  `;
}

function renderQuadrantAddForm(key) {
  const quadrants = getTodoQuadrants();
  const config = quadrants[key] || quadrants.ops;
  return `
    <form class="quadrant-add-form" data-form="quick-task" data-quadrant-add="${escapeHtml(key)}">
      <input class="input" name="title" placeholder="新增${escapeHtml(config.label)}任务" aria-label="新增 TODO" />
      <input type="hidden" name="priority" value="${escapeHtml(config.priority)}" />
      <input class="input" name="due_at" type="datetime-local" value="${defaultTaskDeadlineValue()}" aria-label="DDL" />
      <button class="button" type="submit">添加</button>
      <button class="button secondary" data-action="cancel-quadrant-add" type="button">取消</button>
    </form>
  `;
}

function renderQuickTaskForm() {
  return `
    <form class="todo-capture" data-form="quick-task">
      <input class="input" name="title" placeholder="新增今日 TODO" aria-label="新增今日 TODO" />
      <select class="select" name="priority" aria-label="TODO 优先级">
        ${["normal", "high", "medium", "low"].map((item) => `<option value="${item}">${item}</option>`).join("")}
      </select>
      <input class="input" name="due_at" type="datetime-local" value="${defaultTaskDeadlineValue()}" aria-label="DDL" />
      <button class="button" type="submit">添加</button>
    </form>
  `;
}

function renderTodayTodoPanel() {
  const tasks = todayRelevantTasks();
  return `
    <section class="panel">
      <div class="section-head">
        <div>
          <h2>今日 TODO</h2>
          <p>DDL 精确到分钟，到点会提醒；完成后可以沉淀为长期记忆。</p>
        </div>
        <span class="panel-count">${tasks.length}</span>
      </div>
      ${renderQuickTaskForm()}
      <div class="list compact-list">
        ${tasks.length ? tasks.map(renderTodoCompact).join("") : `<div class="empty">今天没有需要跟进的 TODO</div>`}
      </div>
    </section>
  `;
}

function renderAlarmOverlay() {
  document.querySelector("#alarm-layer")?.remove();
  const task = state.data?.tasks?.find((item) => item.id === state.alarmTaskId);
  if (!task || !isTaskDueNow(task)) return;
  document.body.insertAdjacentHTML("beforeend", `
    <div class="alarm-layer" id="alarm-layer" role="dialog" aria-modal="true" aria-label="TODO 到点提醒">
      <div class="alarm-card">
        <div class="alarm-badge">TODO 到点</div>
        <h2>${escapeHtml(task.title)}</h2>
        <p>${escapeHtml(task.description || "该开始处理这件事了。")}</p>
        <div class="meta-line">
          ${metaPill(`DDL ${formatDeadline(task.due_at)}`, "amber")}
          ${task.project_id ? metaPill(task.project_id, "violet") : ""}
          ${task.priority ? metaPill(task.priority) : ""}
        </div>
        <div class="alarm-actions">
          <button class="button" data-action="complete-task" data-id="${escapeHtml(task.id)}" type="button">完成</button>
          <button class="button secondary" data-action="snooze-task" data-id="${escapeHtml(task.id)}" data-minutes="10" type="button">延时 10 分钟</button>
          <button class="button secondary" data-action="snooze-task" data-id="${escapeHtml(task.id)}" data-minutes="30" type="button">延时 30 分钟</button>
          <button class="button secondary" data-action="snooze-task" data-id="${escapeHtml(task.id)}" data-minutes="60" type="button">延时 1 小时</button>
          <button class="button secondary" data-action="open-task-memory" data-id="${escapeHtml(task.id)}" type="button">记录完成事宜</button>
        </div>
      </div>
    </div>
  `);
}

function renderDailyLogPanel() {
  const log = state.data.today_work_log || { date: workbenchTodayKey(), summary: "", report: "" };
  return `
    <section class="panel daily-log-panel">
      <div class="section-head">
        <div>
          <h2>今日事情总结</h2>
          <p>写给晚上的自己，日报会保留自动生成版本，也允许你改。</p>
        </div>
        ${log.updated_at ? metaPill(`更新 ${formatDate(log.updated_at)}`, "violet") : metaPill("今日未记录", "amber")}
      </div>
      <form class="memo-form" data-form="daily-log">
        <input type="hidden" name="date" value="${escapeHtml(log.date || workbenchTodayKey())}" />
        <div class="form-row">
          <label for="daily-summary">今天真正做成的事</label>
          <textarea class="textarea daily-summary" id="daily-summary" name="summary" placeholder="例如：完成首页重构、确认 Agent 接入边界、沉淀 3 条后续 TODO。">${escapeHtml(log.summary || "")}</textarea>
        </div>
        <div class="form-row">
          <label for="daily-report">每日整理日报</label>
          <textarea class="textarea daily-report" id="daily-report" name="report">${escapeHtml(log.report || "")}</textarea>
        </div>
        <div class="form-actions">
          <button class="button" type="submit">保存今日记录</button>
          <button class="button secondary" data-action="use-generated-report" type="button">恢复自动日报</button>
        </div>
      </form>
    </section>
  `;
}

function renderDailyArchivePanel(compact = true) {
  const archive = state.data.daily_archive || { events: [], assets: [], auto_archived: [], counts: {} };
  const assets = compact ? (archive.assets || []).slice(0, 5) : archive.assets || [];
  return `
    <section class="panel">
      <div class="section-head">
        <div>
          <h2>每日备忘归档</h2>
          <p>${archive.counts?.events || 0} 条输入，${archive.counts?.assets || 0} 条资产。</p>
        </div>
      </div>
      <div class="archive-grid">
        <div class="archive-column">
          <h3>归档资产</h3>
          <div class="list compact-list">
            ${assets.length ? assets.map((asset) => `
              <article class="archive-row archive-asset-card">
                <a class="archive-asset-link" href="${escapeHtml(asset.asset_url)}" target="_blank" rel="noreferrer">
                  <strong>${escapeHtml(asset.title || "归档资产")}</strong>
                  <p>${escapeHtml(summaryPreview(asset.summary, 120) || "已写入本地资产库")}</p>
                </a>
                <div class="archive-asset-actions">
                  <span>${asset.model_used ? "LLM 标题" : escapeHtml(asset.type || "资产")}</span>
                  <button class="button secondary tiny" data-action="remove-ai-archive" data-id="${escapeHtml(asset.id)}" type="button">移除资料</button>
                </div>
              </article>
            `).join("") : `<div class="empty">暂无归档资产</div>`}
          </div>
        </div>
      </div>
    </section>
  `;
}

function renderAgentPipeline(compact = false) {
  const orchestration = state.data.orchestration || {};
  const stages = orchestration.architecture || [];
  const pendingConfirmations = (state.data.confirmations || []).filter((item) => item.decision === "pending");
  return `
    <section class="panel">
      <div class="section-head">
        <div>
          <h2>Agent 编排层</h2>
          <p>${pendingConfirmations.length} 个待确认动作，飞书 Bot 作为主入口，本地工作台负责展示、编辑和确认。</p>
        </div>
        <button class="button secondary" data-view="okr" type="button">查看 OKR</button>
      </div>
      <div class="pipeline ${compact ? "compact" : ""}">
        ${stages.map((stage) => `
          <article class="stage-card">
            <strong>${escapeHtml(stage.title)}</strong>
            <span>${escapeHtml(stage.detail)}</span>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function renderConfirmationRow(item) {
  const payload = item.payload || {};
  return `
    <article class="item-row compact">
      <div class="meta-line">
        ${metaPill(policyLabel(payload.policy), item.risk_level === "high" ? "coral" : item.risk_level === "medium" ? "amber" : "green")}
        ${metaPill(item.action_type || "action")}
        ${metaPill(`风险 ${item.risk_level}`, item.risk_level === "high" ? "coral" : item.risk_level === "medium" ? "amber" : "")}
        ${payload.storage_target ? metaPill(storageTargetLabel(payload.storage_target), "violet") : ""}
        ${metaPill(item.decision)}
        <span>${formatDate(item.updated_at)}</span>
      </div>
      <h3>${escapeHtml(payload.title || item.target_id || "待确认动作")}</h3>
    </article>
  `;
}

function renderAgentRun(run) {
  const output = run.candidate_output || {};
  const candidates = output.candidates || [];
  return `
    <article class="item-row compact">
      <div class="meta-line">
        ${metaPill(run.intent || "capture", "green")}
        ${metaPill(run.status || "candidate_generated")}
        <span>${candidates.length} 个候选</span>
        <span>${formatDate(run.updated_at)}</span>
      </div>
      <h3>${escapeHtml(output.summary || run.intent || "AgentRun")}</h3>
      <p class="content-preview">${escapeHtml(candidates.map((item) => item.title || item.type || "").filter(Boolean).join(" / "))}</p>
    </article>
  `;
}

function renderDashboard() {
  root.innerHTML = `
    <div class="grid-12 dashboard-page">
      <article class="card span-12 dashboard-memo-bar">
        <div class="card-header compact-header">
          <div class="card-title">
            <h3>快速备忘</h3>
            <span>输入会进入 SourceEvent 和今日整理</span>
          </div>
          <span class="pill green">Capture</span>
        </div>
        ${renderQuickMemo("dashboard")}
      </article>
      ${renderDailyBriefCard()}
      ${renderAiSummaryCard()}
      ${renderTodoQuadrants()}
      <div class="span-6">
        ${renderDailyLogPanel()}
      </div>
      <div class="span-6">
        ${renderDailyArchivePanel(true)}
      </div>
    </div>
  `;
}

function okrProfile() {
  const profiles = {
    Q1: {
      subtitle: "打底模型桥接、本地输入闭环和首批知识沉淀",
      goals: [
        { title: "O1：跑通 Ayla 本地 Agent MVP", progress: 86, krs: ["完成快速备忘到候选链路", "建立 SourceEvent / Inbox / TODO 数据闭环", "沉淀 OpenClaw 写入接口"] },
        { title: "O2：建立公开知识与工作沉淀边界", progress: 72, krs: ["区分 PublicKnowledgeVault 与 LocalWorkState", "完成风险确认策略", "形成首批可复用文档"] },
      ],
      insight: ["Q1 的主线是让系统可用，当前闭环已经能稳定承接日常输入。", "风险集中在数据源仍偏手动，需要继续降低录入摩擦。", "建议把常用同步和归档动作做成更短路径入口。"],
    },
    Q2: {
      subtitle: "图文&双列OKR-2026-Q2：业务支撑、性能体验、架构优化、团队建设",
      progressLabel: "Q2规划版",
      source: "飞书 Wiki · revision 446",
      goals: [
        {
          title: "O1：业务支撑",
          status: "规划中",
          krs: [
            "双列规模：折叠屏双列推全，反转实验 LT 打平；探索看后搜切双。",
            "图文体裁：新图文数据口径改造，新动图 Android 侧放开，双端双列放开新图文体裁。",
            "双列框架：优化双列场景交互、统一转场交互扩覆盖、探索筛选提升结果页体验。",
            "双列封面：拉齐封面体验基线，落地画质增强与 VVIC 编码优化，建设图片全链路监控。",
          ],
        },
        {
          title: "O2：性能体验",
          status: "规划中",
          krs: [
            "QOE：DAU 人均双列图文 VV +0.5%，换 query 率 -0.05%，反转实验带动 30MLT +0.02%。",
            "QOS：双端双列 loadmore 有感率对齐单列 -20pp，列表卡顿率 -6pp。",
            "耗时：进内流耗时 -90ms，仅图文详情页耗时 -80ms。",
            "黑白卡率：用户有感黑白卡率 -2pp，推进 QUIC、异步加载、预加载和封面清晰度升档。",
          ],
        },
        {
          title: "O3：架构优化",
          status: "规划中",
          krs: [
            "双列组件化建设与框架能力优化，双端完成框架优化基建预埋。",
            "双端完成组件化接入对齐，图文垂搜卡对齐综搜。",
            "推进双列卡接 KMP、直播卡 NA 化。",
            "图文容器切新框架，无用实验、无用类动态清零。",
          ],
        },
        {
          title: "O4：团队建设（个人）",
          status: "规划中",
          krs: [
            "AI Coding Skill 全团队 KO，规模化推广。",
            "通用 skill 仓定期流水线建设完成。",
            "落地多个 Skill / workflow 场景并追踪到人。",
            "mentor-mentee 持续推进，完成 1 次 one-one。",
          ],
        },
      ],
      insight: [
        "Q2 主线是用双列交互、图文体裁、框架能力和封面专项支撑业务增长，同时用 QOE/QOS 指标约束体验收益。",
        "风险在于目标横跨双端、服务端、测评和多业务线，实验推全、指标口径和专项协同需要持续对齐。",
        "建议看板后续把 O1/O2/O3/O4 映射到任务、实验和文档来源，形成从目标到 KA 的可追踪链路。",
      ],
      roadmap: [
        { lane: "O1 业务支撑", start: 4, end: 6, label: "双列规模 / 图文体裁 / 封面 / 筛选", color: "bar-blue" },
        { lane: "O2 性能体验", start: 4, end: 6, label: "QOE / QOS / 耗时 / 卡顿 / 黑白卡", color: "bar-green" },
        { lane: "O3 架构优化", start: 4, end: 6, label: "组件化 / KMP / NA 化 / 清零", color: "bar-orange" },
        { lane: "O4 团队建设", start: 4, end: 6, label: "AI Coding Skill / mentor-mentee", color: "bar-red" },
      ],
    },
    Q3: {
      subtitle: "从个人工作台走向稳定的数据源和复盘体系",
      goals: [
        { title: "O1：完善工作数据源桥接", progress: 36, krs: ["接入更多只读工作源", "统一候选 schema", "形成同步失败提示"] },
        { title: "O2：季度复盘与周报素材自动化", progress: 30, krs: ["按项目聚合产出", "按风险聚合未完成事项", "生成阶段复盘草稿"] },
      ],
      insight: ["Q3 适合从可视化升级到稳定性建设。", "风险在于数据源权限和字段结构差异。", "可以优先把最常用的两个工作源做深。"],
    },
    Q4: {
      subtitle: "年度复盘、知识网络和可迁移方法论",
      goals: [
        { title: "O1：年度工作资产盘点", progress: 24, krs: ["沉淀关键项目链路", "整理可公开知识", "形成年度影响力材料"] },
        { title: "O2：知识图谱与复用体系", progress: 20, krs: ["补齐标签体系", "形成主题索引", "输出可迁移模板"] },
      ],
      insight: ["Q4 更适合收束与复盘，而不是继续扩功能。", "风险是沉淀粒度不一致导致难检索。", "建议提前约束标签、项目和来源字段。"],
    },
  };
  return profiles[state.okrQuarter] || profiles.Q2;
}

function goalHasProgress(goal) {
  return Number.isFinite(Number(goal.progress));
}

function goalProgressText(goal) {
  if (goal.status) return goal.status;
  return goalHasProgress(goal) ? `${Number(goal.progress)}%` : "规划中";
}

function renderProgressBar(progress, tone = "blue") {
  const gradients = {
    blue: "linear-gradient(90deg, #007aff, #5ac8fa)",
    orange: "linear-gradient(90deg, #ff9500, #ffd60a)",
    red: "linear-gradient(90deg, #ff3b30, #ff6b6b)",
    green: "linear-gradient(90deg, #34c759, #7ee096)",
  };
  return `<div class="progress-track"><div class="progress-fill" style="width:${Math.max(0, Math.min(100, Number(progress) || 0))}%;background:${gradients[tone] || gradients.blue}"></div></div>`;
}

function statusTag(status) {
  if (["已完成", "完成", "稳定"].includes(status)) return "success";
  if (["风险中", "阻塞", "反转"].includes(status)) return "danger";
  if (["进行中", "待确认", "推进中"].includes(status)) return "warn";
  return "";
}

function renderOkr() {
  const profile = okrProfile();
  const activeTasks = (state.data.tasks || []).filter(isActiveTask).slice(0, 6);
  const pendingInbox = (state.data.inbox || []).filter((item) => !["已确认", "已忽略", "已归档", "已发布"].includes(item.status)).slice(0, 5);
  const notes = (state.data.notes || []).slice(0, 5);
  const columns = [
    { title: "重点需求", cls: "success", items: activeTasks.map((task) => ({ title: task.title, desc: taskDescription(task), tag: task.status || "待办" })) },
    { title: "实验", cls: "warn", items: pendingInbox.map((item) => ({ title: item.title, desc: typeLabel(item.item_type), tag: item.status || "待确认" })) },
    { title: "调研任务", cls: "", items: notes.map((note) => ({ title: note.title, desc: note.type || "知识笔记", tag: note.publishable ? "可公开" : "本地" })) },
  ];
  const roadmap = profile.roadmap || [
    { lane: "今日工作台", start: 1, end: 4, label: "体验改版", color: "bar-blue" },
    { lane: "飞书数据源", start: 3, end: 7, label: "日历 / 妙记", color: "bar-green" },
    { lane: "资产化沉淀", start: 5, end: 10, label: "知识与工作索引", color: "bar-orange" },
    { lane: "复盘输出", start: 8, end: 12, label: "阶段总结", color: "bar-red" },
  ];
  const months = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];
  const progressValues = profile.goals.filter(goalHasProgress).map((goal) => Number(goal.progress));
  const average = progressValues.length ? Math.round(progressValues.reduce((sum, progress) => sum + progress, 0) / progressValues.length) : null;
  const progressSummary = average === null ? (profile.progressLabel || "规划版") : `综合推进度 ${average}%`;
  root.innerHTML = `
    <div class="prototype-page okr-page">
      <div class="page-hero">
        <div>
          <h2>${escapeHtml(state.okrYear)} · ${escapeHtml(state.okrQuarter)} OKR 看板</h2>
          <p>${escapeHtml(profile.subtitle)}</p>
        </div>
        <div class="control-row">
          <div class="segmented">
            ${["2025", "2026", "2027"].map((year) => `<button class="${year === state.okrYear ? "active" : ""}" data-action="set-okr-year" data-year="${year}" type="button">${year}</button>`).join("")}
          </div>
          <div class="segmented">
            ${["Q1", "Q2", "Q3", "Q4"].map((quarter) => `<button class="${quarter === state.okrQuarter ? "active" : ""}" data-action="set-okr-quarter" data-quarter="${quarter}" type="button">${quarter}</button>`).join("")}
          </div>
        </div>
      </div>
      <div class="grid-12">
        <article class="card span-7">
          <div class="card-header">
            <div class="card-title"><h3>${escapeHtml(state.okrYear)} · ${escapeHtml(state.okrQuarter)} 季度 OKR</h3><span>${escapeHtml(profile.subtitle)}</span></div>
            <span class="pill">${escapeHtml(progressSummary)}</span>
          </div>
          ${profile.source ? `<div class="source-line">${escapeHtml(profile.source)}</div>` : ""}
          <div class="quarter-okr">
            ${profile.goals.map((goal, index) => `
              <article class="okr-item">
                <div class="okr-top"><strong>${escapeHtml(goal.title)}</strong><span>${escapeHtml(goalProgressText(goal))}</span></div>
                ${goalHasProgress(goal) ? renderProgressBar(goal.progress, index === 0 ? "blue" : index === 1 ? "orange" : "red") : ""}
                <ul class="kr-list">${goal.krs.map((kr) => `<li>${escapeHtml(kr)}</li>`).join("")}</ul>
              </article>
            `).join("")}
          </div>
        </article>
        <article class="card span-5">
          <div class="card-header">
            <div class="card-title"><h3>AI 总结</h3><span>时间窗筛选 · 默认近 ${escapeHtml(state.okrWindow)} 天</span></div>
            <select class="select-input compact-select" data-action="set-okr-window" aria-label="AI 总结时间窗">
              ${["7", "14", "30"].map((value) => `<option value="${value}" ${value === state.okrWindow ? "selected" : ""}>近 ${value} 天</option>`).join("")}
            </select>
          </div>
          <div class="insight-slices">
            ${profile.insight.map((text, index) => `<div class="slice ${index === 0 ? "success" : index === 1 ? "danger" : "idea"}"><strong>${["进度总结", "风险提醒", "灵感启发"][index]}</strong><div>${escapeHtml(text)}</div></div>`).join("")}
          </div>
        </article>
        <article class="card span-12">
          <div class="card-header"><div class="card-title"><h3>进度整理</h3><span>重点需求 / 实验 / 调研任务</span></div><span class="pill">Weekly Grooming</span></div>
          <div class="column-3">
            ${columns.map((col) => `
              <section class="task-column">
                <h4>${escapeHtml(col.title)}</h4>
                ${(col.items.length ? col.items : [{ title: "暂无条目", desc: "当前列还没有可展示内容", tag: "空态" }]).map((item) => `
                  <article class="task-card">
                    <strong>${escapeHtml(item.title)}</strong>
                    <p>${escapeHtml(item.desc || "")}</p>
                    <div class="tag ${statusTag(item.tag) || col.cls}">${escapeHtml(item.tag)}</div>
                  </article>
                `).join("")}
              </section>
            `).join("")}
          </div>
        </article>
        <article class="card span-12 timeline-card">
          <div class="card-header"><div class="card-title"><h3>Roadmap</h3><span>月粒度时间轴 · 3~4 个项目泳道</span></div><span class="pill">2026 Timeline</span></div>
          <div class="timeline-header"><div></div><div class="months-grid">${months.map((month) => `<div class="month-cell">${month}</div>`).join("")}</div></div>
          <div class="roadmap-rows">
            ${roadmap.map((item) => {
              const left = ((item.start - 1) / 12) * 100;
              const width = ((item.end - item.start + 1) / 12) * 100;
              return `<div class="timeline-row"><div><strong>${escapeHtml(item.lane)}</strong></div><div class="timeline-lane"><div class="timeline-bar ${item.color}" style="left:${left}%;width:${width}%">${escapeHtml(item.label)}</div></div></div>`;
            }).join("")}
          </div>
        </article>
      </div>
    </div>
  `;
}

function renderAgent() {
  const orchestration = state.data.orchestration || {};
  const pendingConfirmations = (state.data.confirmations || []).filter((item) => item.decision === "pending");
  const recentRuns = state.data.agent_runs || [];
  root.innerHTML = `
    <div class="stack">
      ${renderAgentPipeline(false)}
      <div class="agent-grid">
        <section class="panel">
          <div class="section-head">
            <div>
              <h2>Agent 角色</h2>
              <p>P0 先跑通本地闭环，P1 再接飞书妙记和日历。</p>
            </div>
          </div>
          <div class="agent-card-grid">
            ${(orchestration.agents || []).map((agent) => `
              <article class="agent-card">
                <div class="meta-line">
                  ${metaPill(agent.priority || "P0", "green")}
                  ${metaPill(agent.stage || "")}
                </div>
                <h3>${escapeHtml(agent.name)}</h3>
                <p>${escapeHtml(agent.status || "")}</p>
              </article>
            `).join("")}
          </div>
        </section>
        <section class="panel">
          <div class="section-head">
            <div>
              <h2>连接器优先级</h2>
              <p>读操作可自动，写操作进入确认队列。</p>
            </div>
          </div>
          <div class="list">
            ${(orchestration.connectors || []).map((connector) => `
              <article class="connector-row">
                <div>
                  <strong>${escapeHtml(connector.name)}</strong>
                  <span>${escapeHtml(connector.mode)}</span>
                </div>
                <div class="meta-line">
                  ${metaPill(connector.priority, "violet")}
                  ${metaPill(connector.status)}
                </div>
              </article>
            `).join("")}
          </div>
        </section>
      </div>
      <div class="agent-grid">
        <section class="panel">
          <div class="section-head">
            <div>
              <h2>人工确认队列</h2>
              <p>低风险本地写入批量确认；DDL、外部写入、删除和公开发布即时或二次确认。</p>
            </div>
          </div>
          <div class="list">
            ${pendingConfirmations.length ? pendingConfirmations.map(renderConfirmationRow).join("") : `<div class="empty">暂无待确认动作</div>`}
          </div>
        </section>
        <section class="panel">
          <div class="section-head">
            <div>
              <h2>最近 AgentRun</h2>
              <p>每次飞书 Bot 或本地 Agent 写入都会留下候选输出和工具调用摘要。</p>
            </div>
          </div>
          <div class="list">
            ${recentRuns.length ? recentRuns.slice(0, 8).map(renderAgentRun).join("") : `<div class="empty">暂无 AgentRun</div>`}
          </div>
        </section>
      </div>
      <section class="panel">
        <div class="section-head">
          <div>
            <h2>落库边界</h2>
            <p>公开知识、工作沉淀和运行缓存分开保存，避免内部资料进入公开 Vault。</p>
          </div>
        </div>
        <div class="storage-grid">
          ${Object.entries(orchestration.storage_roots || {}).map(([key, value]) => `
            <article class="storage-card">
              <strong>${escapeHtml(key)}</strong>
              <span>${escapeHtml(value || "")}</span>
            </article>
          `).join("")}
        </div>
      </section>
    </div>
  `;
}

function renderInbox() {
  const filtered = state.data.inbox.filter((item) => {
    const statusOk = state.inboxFilters.status === "all" || item.status === state.inboxFilters.status;
    const typeOk = state.inboxFilters.type === "all" || item.item_type === state.inboxFilters.type;
    return statusOk && typeOk;
  });
  root.innerHTML = `
    <div class="panel">
      <div class="toolbar">
        <select class="select" data-filter="inbox-status">
          ${["all", "自动分类", "待确认", "需补充", "已确认", "已忽略", "已归档"].map((item) => `<option value="${item}" ${item === state.inboxFilters.status ? "selected" : ""}>${item === "all" ? "全部状态" : item}</option>`).join("")}
        </select>
        <select class="select" data-filter="inbox-type">
          ${["all", "memo", "task_candidate", "summary", "note_candidate", "pinned_candidate"].map((item) => `<option value="${item}" ${item === state.inboxFilters.type ? "selected" : ""}>${item === "all" ? "全部类型" : typeLabel(item)}</option>`).join("")}
        </select>
      </div>
      <div class="list">
        ${filtered.length ? filtered.map(renderInboxItem).join("") : `<div class="empty">当前筛选下没有内容</div>`}
      </div>
    </div>
  `;
}

function renderTasks() {
  const tasks = state.data.tasks.filter((task) => {
    if (state.taskFilter === "done") return task.status === "已完成";
    if (state.taskFilter === "active") return !["已完成", "已取消", "已归档"].includes(task.status);
    return true;
  });
  root.innerHTML = `
    <div class="panel">
      <div class="toolbar">
        <select class="select" data-filter="tasks">
          <option value="active" ${state.taskFilter === "active" ? "selected" : ""}>未完成</option>
          <option value="done" ${state.taskFilter === "done" ? "selected" : ""}>已完成</option>
          <option value="all" ${state.taskFilter === "all" ? "selected" : ""}>全部</option>
        </select>
      </div>
      <div class="list">
        ${tasks.length ? tasks.map(renderTask).join("") : `<div class="empty">暂无 TODO</div>`}
      </div>
    </div>
  `;
}

function sparklinePath(values, width = 240, height = 72) {
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  return values.map((value, index) => {
    const x = (index / (values.length - 1 || 1)) * width;
    const y = height - ((value - min) / span) * (height - 12) - 6;
    return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function renderSparkline(values, index) {
  const line = sparklinePath(values);
  return `
    <svg class="monitor-svg" viewBox="0 0 240 72" aria-hidden="true">
      <path d="${line} L240,72 L0,72 Z" fill="rgba(0,122,255,0.08)"></path>
      <path d="${line}" fill="none" stroke="#007aff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path>
      <circle cx="${240}" cy="${line.split(" ").at(-1)?.split(",")[1] || 36}" r="0" data-index="${index}"></circle>
    </svg>
  `;
}

function libraExperimentUrl(exp) {
  if (exp.url) return exp.url;
  if (!exp.id) return "https://data.bytedance.net/libra/flights?app_id=-1&owner_type=my&page=1&page_size=50&search_type=fuzzy";
  return `https://data.bytedance.net/datatester/app/${encodeURIComponent(exp.app_id || "-1")}/experiment/${encodeURIComponent(exp.id)}/detail`;
}

function renderLibraExperiments() {
  const payload = state.libraExperiments;
  const experiments = (payload?.experiments || []).filter((exp) => exp.status === "进行中" || Number(exp.status_code) === 1);
  if (state.libraExperimentsLoading && !payload) {
    return `
      <div class="experiment-list is-loading">
        <div class="experiment-list-head"><span>实验名称</span><span>创建时间</span><span>实验标签</span></div>
        ${Array.from({ length: 3 }).map(() => `
          <div class="experiment-row skeleton-row">
            <span></span><span></span><span></span>
          </div>
        `).join("")}
      </div>
    `;
  }
  if (state.libraExperimentsError && !experiments.length) {
    return `
      <div class="empty experiment-empty">
        <strong>Libra 实验读取失败</strong>
        <span>${escapeHtml(state.libraExperimentsError)}</span>
      </div>
    `;
  }
  if (!payload) {
    return `
      <div class="empty experiment-empty">
        <strong>准备读取 Libra 实验</strong>
        <span>将复用当前 Chrome 登录态，仅拉取当前授权账号名下运行中的实验。</span>
      </div>
    `;
  }
  if (!experiments.length) {
    return `
      <div class="empty experiment-empty">
        <strong>暂无运行中的重点实验</strong>
        <span>${payload.updated_at ? `最后检查：${formatDate(payload.updated_at)}` : "当前筛选条件没有返回运行中实验。"}</span>
      </div>
    `;
  }
  const recycle = payload.recycle_todos;
  const recycleText = recycle
    ? `超 15 天 ${Number(recycle.eligible || 0)} 个 · 今日回收 TODO ${Number(recycle.created || 0) + Number(recycle.skipped?.duplicate_today || 0)} 条`
    : "";
  return `
    <div class="experiment-list">
      <div class="experiment-list-head"><span>实验名称</span><span>创建时间</span><span>实验标签</span></div>
      ${experiments.map((exp) => {
        const url = libraExperimentUrl(exp);
        const label = exp.reversal_label || (exp.is_reversal ? "反转实验" : "普通实验");
        return `
          <article class="experiment-row">
            <a class="experiment-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">
              <strong>${escapeHtml(exp.name || "未命名实验")}</strong>
              <span>ID ${escapeHtml(exp.id || "")}${exp.layer_name ? ` · ${escapeHtml(exp.layer_name)}` : ""}</span>
            </a>
            <div class="experiment-time">${escapeHtml(formatDate(exp.created_time || exp.start_time))}</div>
            <div class="experiment-tags">
              <span class="tag ${exp.is_reversal ? "danger" : ""}">${escapeHtml(label)}</span>
            </div>
          </article>
        `;
      }).join("")}
      <div class="experiment-footnote">
        <span>${escapeHtml(payload.cached ? "使用本地短缓存" : "实时读取")}</span>
        <span>${escapeHtml(recycleText || (payload.updated_at ? formatDate(payload.updated_at) : ""))}</span>
      </div>
    </div>
  `;
}

function renderMemos() {
  const notes = state.data.notes || [];
  const events = state.data.events || [];
  const runs = state.data.agent_runs || [];
  const commands = [
    { title: "启动本地工作台", code: "python3 server.py --host 127.0.0.1 --port 5183" },
    { title: "检查前端语法", code: "node --check web/app.js" },
    { title: "读取当前状态", code: "curl -s http://127.0.0.1:5183/api/state" },
    { title: "读取飞书文档", code: "lark-cli docs +fetch --api-version v2 --doc <url>" },
  ];
  const docs = notes.slice(0, 5).map((note) => ({
    title: note.title,
    updated: formatDate(note.updated_at || note.created_at),
    platform: note.publishable ? "Public Vault" : "Local State",
  }));
  const boards = [
    { title: "Ayla 本地状态", desc: "SourceEvent / TODO / Notes", metrics: [`输入 ${events.length}`, `笔记 ${notes.length}`] },
    { title: "AgentRun 候选", desc: "模型整理与确认队列", metrics: [`运行 ${runs.length}`, `待确认 ${(state.data.confirmations || []).filter((item) => item.decision === "pending").length}`] },
    { title: "飞书数据源", desc: "Calendar / Minutes 只读同步", metrics: [state.larkStatus?.auth?.ok ? "账号已连" : "待检查", `${state.data.settings?.lark_sync_days || 7} 天窗口`] },
  ];
  const monitors = [
    { title: "待整理候选", value: state.data.stats?.pending_inbox || 0, trend: [4, 5, 5, 7, 6, state.data.stats?.pending_inbox || 0] },
    { title: "未完成 TODO", value: state.data.stats?.today_tasks || 0, trend: [3, 4, 6, 5, 7, state.data.stats?.today_tasks || 0] },
    { title: "知识笔记", value: state.data.stats?.notes || 0, trend: [1, 2, 3, 4, 4, state.data.stats?.notes || 0] },
  ];
  root.innerHTML = `
    <div class="prototype-page memo-page">
      <div class="grid-12">
        <article class="card span-12">
          <div class="card-header"><div class="card-title"><h3>个人命令备忘</h3><span>常用 git / lark / 本地服务片段，便签纸质感展示</span></div><span class="pill">高频命令</span></div>
          <div class="sticky-grid">
            ${commands.map((item) => `
              <button class="sticky-note" data-action="copy-command" data-command="${escapeHtml(item.code)}" type="button">
                <strong>${escapeHtml(item.title)}</strong>
                <code>${escapeHtml(item.code)}</code>
              </button>
            `).join("")}
          </div>
        </article>
        <article class="card span-6">
          <div class="card-header"><div class="card-title"><h3>重点文档索引</h3><span>常看的方案、周报、沉淀笔记</span></div><span class="pill">近期高频</span></div>
          <div class="doc-list">
            ${(docs.length ? docs : [{ title: "暂无重点文档", updated: "待沉淀", platform: "Local" }]).map((doc) => `
              <article class="doc-item">
                <div class="doc-main"><strong>${escapeHtml(doc.title)}</strong><span>最后更新：${escapeHtml(doc.updated)}</span></div>
                <div class="doc-jump"><span class="platform-badge">${escapeHtml(doc.platform)}</span><span>↗</span></div>
              </article>
            `).join("")}
          </div>
        </article>
        <article class="card span-6">
          <div class="card-header"><div class="card-title"><h3>重点业务看板</h3><span>核心工作入口与缩略指标</span></div><span class="pill">Live Metrics</span></div>
          <div class="business-grid">
            ${boards.map((board) => `
              <article class="board-item">
                <strong>${escapeHtml(board.title)}</strong>
                <span>${escapeHtml(board.desc)}</span>
                <div class="board-metric">${board.metrics.map((metric) => `<span>${escapeHtml(metric)}</span>`).join("")}</div>
              </article>
            `).join("")}
          </div>
        </article>
        <article class="card span-6">
          <div class="card-header">
            <div class="card-title"><h3>重点实验</h3><span>当前授权账号 · 仅运行中</span></div>
            <button class="button secondary tiny" data-action="refresh-libra-experiments" ${state.libraExperimentsLoading ? "disabled" : ""} type="button">${state.libraExperimentsLoading ? "读取中" : "刷新"}</button>
          </div>
          ${renderLibraExperiments()}
        </article>
        <article class="card span-6">
          <div class="card-header"><div class="card-title"><h3>重点监控</h3><span>含迷你趋势图</span></div><span class="pill">Monitor</span></div>
          <div class="monitor-grid">
            ${monitors.map((item, index) => `
              <article class="monitor-card">
                <div class="monitor-head"><div><strong>${escapeHtml(item.title)}</strong><span>${index === 0 ? "需要整理" : index === 1 ? "跟进中" : "持续增长"}</span></div><div>${escapeHtml(item.value)}</div></div>
                ${renderSparkline(item.trend, index)}
              </article>
            `).join("")}
          </div>
        </article>
      </div>
    </div>
  `;
}

function renderNoteButton(note) {
  const active = note.id === state.selectedNoteId ? "active" : "";
  const tags = Array.isArray(note.tags) ? note.tags.slice(0, 3) : [];
  return `
    <button class="note-button ${active}" data-action="select-note" data-id="${escapeHtml(note.id)}" type="button">
      <strong>${escapeHtml(note.title)}</strong>
      <span class="meta-line">
        ${metaPill(note.type || "笔记", "green")}
        ${tags.map((tag) => metaPill(`#${tag}`)).join("")}
      </span>
    </button>
  `;
}

function renderAssetGraphMini() {
  const graph = graphData();
  if (!graph.nodes.length) {
    return `<div class="empty">暂无图谱节点</div>`;
  }
  const width = 540;
  const height = 280;
  const cx = width / 2;
  const cy = height / 2;
  const radius = 92;
  const positions = new Map();
  graph.nodes.slice(0, 12).forEach((node, index, list) => {
    const angle = (Math.PI * 2 * index) / list.length - Math.PI / 2;
    positions.set(node.id, { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius });
  });
  const color = (type) => (type === "project" ? "#34c759" : type === "tag" ? "#ff9500" : "#007aff");
  return `
    <svg class="asset-graph-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="知识图谱">
      ${graph.links.slice(0, 18).map((link) => {
        const a = positions.get(link.source);
        const b = positions.get(link.target);
        if (!a || !b) return "";
        return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" />`;
      }).join("")}
      ${graph.nodes.slice(0, 12).map((node) => {
        const point = positions.get(node.id);
        const label = node.label.length > 8 ? `${node.label.slice(0, 7)}…` : node.label;
        return `<g transform="translate(${point.x}, ${point.y})"><circle r="20" fill="${color(node.type)}"></circle><text y="38">${escapeHtml(label)}</text></g>`;
      }).join("")}
    </svg>
  `;
}

function renderKnowledge() {
  const notes = state.data.notes || [];
  const settings = state.data.settings || {};
  const memories = state.data.agent_memories || [];
  const spaces = state.data.knowledge_spaces || [];
  const localAssets = notes.filter((note) => !note.publishable).slice(0, 5);
  const publicAssets = notes.filter((note) => note.publishable).slice(0, 5);
  const activeMemories = memories.filter((memory) => memory.status === "active");
  const pendingArchive = (state.data.inbox || []).filter((item) => ["note_candidate", "work_record_candidate", "report_material_candidate"].includes(item.item_type)).slice(0, 4);
  const categories = [
    ["技术方案", notes.filter((note) => /方案|设计|技术/.test(note.title)).length],
    ["周报", notes.filter((note) => /周报|日报|总结/.test(note.title)).length],
    ["会议纪要", (state.data.events || []).filter((event) => event.source_type === "lark_minutes").length],
    ["OKR", notes.filter((note) => /OKR|目标|复盘/.test(note.title)).length],
  ];
  const workDocs = [...pendingArchive, ...notes].slice(0, 6).map((item) => ({
    id: item.id,
    title: item.title,
    time: formatDate(item.updated_at || item.created_at || item.collected_at),
    tag: item.item_type ? typeLabel(item.item_type) : item.publishable ? "公开知识" : "工作沉淀",
    ai: Boolean(item.item_type),
  }));
  root.innerHTML = `
    <div class="prototype-page assets-page">
      <div class="grid-12">
        <article class="card span-12">
          <div class="card-header"><div class="card-title"><h3>资产看板</h3><span>人看的资产视图与 Agent 读取的持久层分开管理</span></div><span class="pill">Double Space</span></div>
          <div class="agent-card-grid">
            <article class="agent-card">
              <div class="meta-line">${metaPill("AgentMemory", "violet")}${metaPill(`${activeMemories.length} 条 active`)}</div>
              <h3>AI 读的长期记忆</h3>
              <p>固定便笺不进入 Agent context；这里保存偏好、规则、项目上下文、工具用法和可持续迭代的自学习结果。</p>
              <ul class="asset-list compact-list">
                ${activeMemories.slice(0, 4).map((memory) => `
                  <li>
                    <div class="asset-meta"><strong>${escapeHtml(memory.title)}</strong><small>${escapeHtml(memory.scenario || "global")} · ${escapeHtml(memory.memory_type || "memory")} · v${escapeHtml(memory.version || 1)}</small></div>
                    <span class="pill">${escapeHtml(memory.scope || "global")}</span>
                  </li>
                `).join("") || `<li><div class="asset-meta"><strong>暂无 Agent 记忆</strong><small>从收件箱确认 memory_candidate 后写入</small></div></li>`}
              </ul>
            </article>
            <article class="agent-card">
              <div class="meta-line">${metaPill("Knowledge Spaces", "green")}${metaPill(`${spaces.length} 个场景`)}</div>
              <h3>知识库分类存储</h3>
              <p>长内容按 work / coding / research / personal / public 分场景落盘，Agent 只按需读取摘要和索引。</p>
              <div class="doc-category">
                ${spaces.map((space) => `<span class="pill">${escapeHtml(space.name)} · ${escapeHtml(space.storage_target)}</span>`).join("")}
              </div>
            </article>
            <article class="agent-card">
              <div class="meta-line">${metaPill("Context Pack", "amber")}</div>
              <h3>场景化上下文</h3>
              <p><code>/api/agent/context?scenario=coding&project=ayla</code> 只返回 AgentMemory、知识库索引和策略，不返回人看的固定便笺。</p>
            </article>
          </div>
          <div class="asset-columns">
            <section class="asset-column">
              <div class="card inner-card">
                <div class="card-header"><div class="card-title"><h3>个人列 · 本地资产</h3><span>文件、笔记、日志归档</span></div><span class="pill">Local Vault</span></div>
                <ul class="asset-list">
                  ${(publicAssets.length ? publicAssets : localAssets).slice(0, 5).map((note) => `
                    <li>
                      <div class="asset-meta"><strong>${escapeHtml(note.title)}</strong><small>${escapeHtml(note.type || "知识笔记")} · ${formatDate(note.updated_at || note.created_at)}</small></div>
                      <button class="plain-jump" data-action="select-note" data-id="${escapeHtml(note.id)}" type="button">↗</button>
                    </li>
                  `).join("") || `<li><div class="asset-meta"><strong>暂无资产</strong><small>先从今日看板归档资料</small></div></li>`}
                </ul>
              </div>
              <div class="card inner-card">
                <div class="card-header"><div class="card-title"><h3>Obsidian 桥接状态</h3><span>状态模拟，可视化连接反馈</span></div><span class="pill">Bridge</span></div>
                <div class="bridge-status"><div><strong>${settings.public_vault_path ? "已配置 · PublicKnowledgeVault" : "未连接"}</strong><div class="muted">路径：${escapeHtml(settings.public_vault_path || "待配置")}</div></div><div class="bridge-light">${settings.public_vault_path ? "●" : "○"}</div></div>
              </div>
              <div class="card inner-card graph-wrap">
                <div class="card-header"><div class="card-title"><h3>知识图谱</h3><span>节点基于笔记项目与标签生成</span></div><span class="pill">Graph</span></div>
                ${renderAssetGraphMini()}
              </div>
            </section>
            <section class="asset-column">
              <div class="card inner-card">
                <div class="card-header"><div class="card-title"><h3>工作列 · 飞书文档智能分类</h3><span>按技术方案 / 周报 / 会议纪要 / OKR 组织</span></div><span class="pill">Feishu Smart Tags</span></div>
                <div class="doc-category">
                  ${categories.map(([name, count]) => `<span class="pill">${escapeHtml(name)} · ${count}</span>`).join("")}
                </div>
              </div>
              <div class="card inner-card">
                <div class="card-header"><div class="card-title"><h3>可视化索引</h3><span>按主题快速定位近期高价值内容</span></div><span class="pill">Smart Index</span></div>
                <div class="doc-card-grid">
                  ${workDocs.length ? workDocs.map((doc) => `
                    <article class="doc-mini-card">
                      <strong>${escapeHtml(doc.title)}</strong>
                      <span>${escapeHtml(doc.time)}</span>
                      <div class="tag ${doc.ai ? "warn" : ""}">${doc.ai ? "AI 归档" : escapeHtml(doc.tag)}</div>
                    </article>
                  `).join("") : `<div class="empty">暂无可视化索引</div>`}
                </div>
              </div>
              <div class="card inner-card">
                <div class="card-header"><div class="card-title"><h3>快捷跳转按钮</h3><span>常用工作流入口</span></div><span class="pill">Quick Access</span></div>
                <div class="quick-actions">
                  <button class="jump-button" data-action="copy-agent-token" type="button">复制 Agent Token</button>
                  <button class="jump-button" data-view="memos" type="button">查看备忘入口</button>
                  <button class="jump-button" data-view="settings" type="button">配置飞书同步</button>
                  <button class="jump-button" data-action="check-lark" type="button">检查 lark-cli</button>
                </div>
              </div>
            </section>
          </div>
        </article>
      </div>
    </div>
  `;
}

function graphData() {
  const nodes = [];
  const nodeMap = new Map();
  const links = [];
  const addNode = (id, label, type) => {
    if (!id || nodeMap.has(id)) return;
    nodeMap.set(id, { id, label, type });
    nodes.push(nodeMap.get(id));
  };
  const addLink = (source, target) => {
    if (!source || !target || source === target) return;
    links.push({ source, target });
  };
  for (const note of state.data.notes) {
    addNode(note.id, note.title, "note");
    const projects = Array.isArray(note.projects) ? note.projects : [];
    for (const project of projects) {
      const projectId = `project:${project}`;
      addNode(projectId, project, "project");
      addLink(note.id, projectId);
    }
    const tags = Array.isArray(note.tags) ? note.tags : [];
    for (const tag of tags.slice(0, 2)) {
      const tagId = `tag:${tag}`;
      addNode(tagId, `#${tag}`, "tag");
      addLink(note.id, tagId);
    }
  }
  return { nodes: nodes.slice(0, 28), links: links.slice(0, 42) };
}

function renderGraph() {
  const graph = graphData();
  if (!graph.nodes.length) {
    root.innerHTML = `<div class="empty">暂无图谱节点</div>`;
    return;
  }
  const width = 980;
  const height = 560;
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.36;
  const positions = new Map();
  graph.nodes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / graph.nodes.length - Math.PI / 2;
    positions.set(node.id, {
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    });
  });
  const color = (type) => {
    if (type === "project") return "#1f7a6b";
    if (type === "task") return "#c85b45";
    if (type === "tag") return "#9f7417";
    return "#5b5f97";
  };
  root.innerHTML = `
    <div class="graph-canvas">
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="知识图谱">
        ${graph.links.map((link) => {
          const a = positions.get(link.source);
          const b = positions.get(link.target);
          if (!a || !b) return "";
          return `<line class="graph-line" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" />`;
        }).join("")}
        ${graph.nodes.map((node) => {
          const point = positions.get(node.id);
          const label = node.label.length > 12 ? `${node.label.slice(0, 11)}…` : node.label;
          return `
            <g class="graph-node" transform="translate(${point.x}, ${point.y})">
              <circle r="24" fill="${color(node.type)}"></circle>
              <text y="42">${escapeHtml(label)}</text>
            </g>
          `;
        }).join("")}
      </svg>
    </div>
  `;
}

function renderSettings() {
  const settings = state.data.settings;
  const modelStatus = state.data.model_cli_status || {};
  const larkStatus = state.larkStatus;
  const larkAuth = larkStatus?.auth || {};
  const larkScopes = larkStatus?.scope_check || {};
  const feishuEnabled = settingEnabled(settings.feishu_enabled);
  const syncDays = Number(settings.lark_sync_days || 7);
  const assetRootPath = settings.asset_root_path || String(settings.state_root_path || "").replace(/\/LocalWorkState\/?$/, "") || "";
  const prefs = state.prefs;
  const profile = state.data.profile || {};
  const bindingProvider = profile.bound ? "飞书授权人" : "本地演示身份";
  const bindingReady = profile.bound && larkStatus?.auth?.ok && larkStatus?.scope_check?.ok;
  root.innerHTML = `
    <div class="prototype-page settings-page">
      <form class="settings-board" data-form="settings">
        <div class="grid-12">
          <article class="card settings-card span-6">
            <div class="card-header">
              <div class="card-title"><h3>模型桥接</h3><span>整理引擎、CLI 命令和超时策略</span></div>
              ${metaPill(modelStatus.available ? "命令可用" : "待检查", modelStatus.available ? "green" : "amber")}
            </div>
            <div class="settings-fields">
              <div class="inline-fields">
                <label>整理引擎
                  <select class="select-input" name="model_provider">
                    ${[
                      ["model_cli", "真实模型 CLI"],
                      ["manual-rules", "本地规则兜底"],
                    ].map(([value, label]) => `<option value="${value}" ${value === (settings.model_provider || "manual-rules") ? "selected" : ""}>${label}</option>`).join("")}
                  </select>
                </label>
                <label>CLI
                  <select class="select-input" name="model_cli">
                    ${["codex", "claude"].map((item) => `<option value="${item}" ${item === (settings.model_cli || "codex") ? "selected" : ""}>${item}</option>`).join("")}
                  </select>
                </label>
                <label>超时
                  <input class="text-input" name="model_cli_timeout_seconds" type="number" min="5" max="180" value="${escapeHtml(settings.model_cli_timeout_seconds || 45)}" />
                </label>
              </div>
              <label>Codex Model<input class="text-input" name="codex_model" value="${escapeHtml(settings.codex_model || "")}" /></label>
              <label>Claude Model<input class="text-input" name="claude_model" value="${escapeHtml(settings.claude_model || "")}" /></label>
              <label>自定义模型命令<input class="text-input token-input" name="model_cli_command" value="${escapeHtml(settings.model_cli_command || "")}" placeholder="留空使用内置命令；命令从 stdin 读取 prompt" /></label>
              <div class="model-status-line">
                ${metaPill(modelStatus.enabled ? "已启用" : "未启用", modelStatus.enabled ? "green" : "")}
                ${metaPill(modelStatus.provider || "codex", "violet")}
                ${metaPill(modelStatus.available ? "命令可用" : "命令不可用", modelStatus.available ? "green" : "coral")}
              </div>
              <div class="note-path">${escapeHtml(modelStatus.command || "模型命令未配置")}</div>
            </div>
          </article>

          <article class="card settings-card span-6">
            <div class="card-header">
              <div class="card-title"><h3>UI 模式</h3><span>主题、默认窗口和 OKR 周期</span></div>
              ${metaPill(state.theme === "dark" ? "深色" : "浅色", "violet")}
            </div>
            <div class="settings-fields">
              <div class="segmented settings-segment">
                ${[
                  ["light", "浅色"],
                  ["dark", "深色"],
                  ["system", "跟随系统"],
                ].map(([value, label]) => `<button class="${value === prefs.uiMode ? "active" : ""}" data-action="set-pref" data-pref="uiMode" data-value="${value}" type="button">${label}</button>`).join("")}
              </div>
              <div class="inline-fields">
                <label>默认时间窗
                  <select class="select-input" data-pref="defaultWindow">
                    ${["7", "14", "30"].map((value) => `<option value="${value}" ${value === prefs.defaultWindow ? "selected" : ""}>近 ${value} 天</option>`).join("")}
                  </select>
                </label>
                <label>OKR 周期
                  <select class="select-input" data-pref="okrCycle">
                    ${["季度", "半年", "年度"].map((value) => `<option value="${value}" ${value === prefs.okrCycle ? "selected" : ""}>${value}</option>`).join("")}
                  </select>
                </label>
                <label>同步频率
                  <select class="select-input" data-pref="syncFrequency">
                    ${["手动", "每 30 分钟", "每日一次"].map((value) => `<option value="${value}" ${value === prefs.syncFrequency ? "selected" : ""}>${value}</option>`).join("")}
                  </select>
                </label>
              </div>
            </div>
          </article>

          <article class="card settings-card span-12">
            <div class="card-header">
              <div class="card-title"><h3>TODO 设置</h3><span>四象限名称和强调色，保存到本地工作台偏好</span></div>
              <span class="pill">Priority Matrix</span>
            </div>
            <div class="todo-pref-grid">
              ${[
                ["quadHighLabel", "colorHigh", "第一象限", prefs.quadHighLabel],
                ["quadFocusLabel", "", "第二象限", prefs.quadFocusLabel],
                ["quadOpsLabel", "colorMedium", "第三象限", prefs.quadOpsLabel],
                ["quadLowLabel", "colorLow", "第四象限", prefs.quadLowLabel],
              ].map(([labelKey, colorKey, title, value]) => `
                <label class="todo-pref-card">
                  <span>${escapeHtml(title)}</span>
                  <input class="text-input" data-pref="${labelKey}" value="${escapeHtml(value)}" />
                  ${colorKey ? `<input class="color-input" data-pref="${colorKey}" type="color" value="${escapeHtml(prefs[colorKey])}" aria-label="${escapeHtml(title)}颜色" />` : `<span class="color-spacer"></span>`}
                </label>
              `).join("")}
            </div>
          </article>

          <article class="card settings-card span-12 account-binding-card">
            <div class="card-header">
              <div class="card-title"><h3>工作账号绑定</h3><span>扫码绑定飞书账号，后续资料归属写入授权人真实信息</span></div>
              ${metaPill(bindingReady ? "绑定完成" : profile.bound ? "权限待检查" : "待绑定", bindingReady ? "green" : "amber")}
            </div>
            <div class="account-binding-layout">
              <section class="account-profile-panel">
                <div class="account-avatar">${escapeHtml(profile.avatar || "AY")}</div>
                <div>
                  <span class="eyebrow">${escapeHtml(bindingProvider)}</span>
                  <h4>${escapeHtml(profile.display_name || "本地用户")}</h4>
                  <p>${escapeHtml(profile.handle || "@ayla.local")}</p>
                  <div class="model-status-line">
                    ${metaPill(profile.provider || "demo", profile.bound ? "green" : "")}
                    ${profile.bound_at ? metaPill(`绑定于 ${formatDate(profile.bound_at)}`, "violet") : metaPill("未绑定真实账号", "amber")}
                  </div>
                </div>
              </section>
              ${renderPermissionChecklist(larkStatus, profile)}
              ${renderLarkBindingSession(state.larkBinding)}
            </div>
            <div class="quick-actions">
              <button class="jump-button primary" data-action="start-lark-binding" type="button">扫码绑定飞书</button>
              <button class="jump-button" data-action="complete-lark-binding" type="button" ${state.larkBinding?.device_code ? "" : "disabled"}>完成绑定</button>
              <button class="jump-button" data-action="claim-lark-account" type="button">绑定当前认证</button>
              <button class="jump-button" data-action="check-lark" type="button">刷新权限</button>
            </div>
          </article>

          <article class="card settings-card span-6">
            <div class="card-header">
              <div class="card-title"><h3>飞书数据源</h3><span>通过 lark-cli 只读同步日历和妙记</span></div>
              ${metaPill(feishuEnabled ? "已启用" : "未启用", feishuEnabled ? "green" : "")}
            </div>
            <div class="settings-fields">
              <div class="inline-fields">
                <label>开关
                  <select class="select-input" name="feishu_enabled">
                    ${[
                      ["true", "启用"],
                      ["false", "停用"],
                    ].map(([value, label]) => `<option value="${value}" ${settingEnabled(value) === feishuEnabled ? "selected" : ""}>${label}</option>`).join("")}
                  </select>
                </label>
                <label>同步天数
                  <input class="text-input" name="lark_sync_days" type="number" min="1" max="31" value="${escapeHtml(settings.lark_sync_days || 7)}" />
                </label>
              </div>
              <label>lark-cli 命令<input class="text-input token-input" name="lark_cli_path" value="${escapeHtml(settings.lark_cli_path || "lark-cli")}" /></label>
              <div class="bridge-status compact">
                <div>
                  <strong>${larkAuth.ok ? `账号 ${escapeHtml(larkAuth.user_name || "")}` : larkStatus ? "账号待处理" : "尚未检查"}</strong>
                  <div class="muted">${escapeHtml(larkStatus ? `${larkStatus.command || ""} ${larkStatus.version || ""}`.trim() : settings.lark_cli_path || "lark-cli")}</div>
                </div>
                <div class="bridge-light">${larkStatus?.available ? "●" : "○"}</div>
              </div>
              <div class="model-status-line">
                ${larkStatus ? metaPill(larkStatus.available ? "命令可用" : "命令不可用", larkStatus.available ? "green" : "coral") : metaPill("尚未检查", "amber")}
                ${larkScopes.ok ? metaPill("scope 已授权", "green") : larkStatus ? metaPill("scope 待处理", "amber") : ""}
              </div>
              ${larkStatus && (!larkAuth.ok || !larkScopes.ok) ? `<p class="content-preview">${escapeHtml(larkAuth.message || larkScopes.message || larkStatus.error || "连接未完全就绪")}</p>` : ""}
              <div class="quick-actions">
                <button class="jump-button" data-action="check-lark" type="button">检查连接</button>
                <button class="jump-button primary" data-action="sync-lark-today" type="button">同步今天</button>
                <button class="jump-button" data-action="sync-lark-range" type="button">同步近 ${escapeHtml(syncDays)} 天</button>
              </div>
            </div>
          </article>

          <article class="card settings-card span-6">
            <div class="card-header">
              <div class="card-title"><h3>本地资产路径</h3><span>默认项目根目录，可自定义本地落库地址</span></div>
              <span class="pill">Local First</span>
            </div>
            <div class="settings-fields">
              <label>本地资产根目录<input class="text-input token-input" name="asset_root_path" value="${escapeHtml(assetRootPath)}" /></label>
              <p class="muted">默认放在项目目录下的 <code>agent-vault/</code>，已被 <code>.gitignore</code> 忽略，不上传 GitHub。</p>
              <label>本地 State Root<input class="text-input token-input" readonly value="${escapeHtml(settings.state_root_path || "")}" /></label>
              <label>公开知识 Vault<input class="text-input token-input" readonly value="${escapeHtml(settings.public_vault_path || settings.vault_path || "")}" /></label>
              <label>本地工作库<input class="text-input token-input" readonly value="${escapeHtml(settings.work_library_path || "")}" /></label>
              <label>GitHub 仓库<input class="text-input token-input" name="github_repo" value="${escapeHtml(settings.github_repo || "")}" /></label>
            </div>
          </article>

          <article class="card settings-card span-6">
            <div class="card-header">
              <div class="card-title"><h3>Agent 通信</h3><span>OpenClaw 写入 Token 与确认边界</span></div>
              <span class="pill">Secure Local API</span>
            </div>
            <div class="settings-fields">
              <label>OpenClaw 写入 Token<input class="text-input token-input" readonly value="${escapeHtml(settings.agent_api_token || "")}" /></label>
              <label>摘要频率
                <select class="select-input" name="summary_frequency">
                  ${["manual", "daily", "twice_daily"].map((item) => `<option value="${item}" ${item === settings.summary_frequency ? "selected" : ""}>${item}</option>`).join("")}
                </select>
              </label>
              <div class="quick-actions">
                <button class="jump-button" data-action="copy-agent-token" type="button">复制 Token</button>
                <button class="jump-button danger" data-action="rotate-agent-token" type="button">重置 Token</button>
              </div>
            </div>
          </article>

          <article class="card settings-card span-6">
            <div class="card-header">
              <div class="card-title"><h3>审计日志</h3><span>最近本地写入与同步动作</span></div>
              ${metaPill(`${state.data.audit_logs.length} 条`)}
            </div>
            <div class="audit-list">
              ${state.data.audit_logs.length ? state.data.audit_logs.slice(0, 6).map((log) => `
                <article class="audit-row">
                  <div>${metaPill(log.action, "green")}<span>${formatDate(log.created_at)}</span></div>
                  <p>${escapeHtml(log.target_type || "")} ${escapeHtml(log.target_id || "")}</p>
                </article>
              `).join("") : `<div class="empty">暂无审计日志</div>`}
            </div>
          </article>
        </div>
        <div class="settings-save-bar">
          <button class="button" type="submit">保存设置</button>
          <span>页面偏好会即时写入本地，服务端设置点击保存后生效。</span>
        </div>
      </form>
    </div>
  `;
}

function render() {
  if (!state.data) return;
  state.view = normalizeView(state.view);
  titleEl.textContent = titleByView[state.view] || "个人 Agent 工作台";
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.view);
  });
  maybeLoadLibraExperiments();
  if (state.view === "dashboard") renderDashboard();
  if (state.view === "okr") renderOkr();
  if (state.view === "memos") renderMemos();
  if (state.view === "knowledge") renderKnowledge();
  if (state.view === "settings") renderSettings();
  renderAlarmOverlay();
  maybeLoadLarkStatus();
}

async function refresh(message = "", toastOptions = {}) {
  await loadState();
  render();
  if (message) showToast(message, toastOptions);
}

document.addEventListener("click", async (event) => {
  const navButton = event.target.closest("[data-view]");
  if (navButton) {
    setView(navButton.dataset.view);
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) return;
  const action = actionButton.dataset.action;
  const id = actionButton.dataset.id;

  try {
    if (action === "refresh") {
      await refresh("已刷新");
      return;
    }
    if (action === "toggle-theme") {
      state.prefs.uiMode = state.theme === "dark" ? "light" : "dark";
      applyChromeState();
      saveWorkbenchPrefs();
      showToast(state.theme === "dark" ? "已切到深色" : "已切到浅色");
      return;
    }
    if (action === "set-pref") {
      state.prefs[actionButton.dataset.pref] = actionButton.dataset.value || "";
      applyChromeState();
      saveWorkbenchPrefs();
      render();
      showToast("偏好已保存");
      return;
    }
    if (action === "set-okr-year") {
      state.okrYear = actionButton.dataset.year || state.okrYear;
      localStorage.setItem("aylaOkrYear", state.okrYear);
      render();
      return;
    }
    if (action === "set-okr-quarter") {
      state.okrQuarter = actionButton.dataset.quarter || state.okrQuarter;
      localStorage.setItem("aylaOkrQuarter", state.okrQuarter);
      render();
      return;
    }
    if (action === "copy-command") {
      await navigator.clipboard.writeText(actionButton.dataset.command || "");
      showToast("命令已复制");
      return;
    }
    if (action === "toggle-sidebar") {
      state.sidebarCollapsed = !state.sidebarCollapsed;
      localStorage.setItem("aylaSidebarCollapsed", String(state.sidebarCollapsed));
      applyChromeState();
      return;
    }
    if (action === "open-quadrant-add") {
      state.todoAddQuadrant = actionButton.dataset.quadrant || "high";
      render();
      window.setTimeout(() => {
        document.querySelector(`[data-quadrant-add="${CSS.escape(state.todoAddQuadrant)}"] input[name="title"]`)?.focus();
      }, 0);
      return;
    }
    if (action === "cancel-quadrant-add") {
      state.todoAddQuadrant = "";
      render();
      return;
    }
    if (action === "edit-task") {
      state.taskEditId = id;
      state.taskMemoryId = "";
      render();
      return;
    }
    if (action === "cancel-task-edit") {
      state.taskEditId = "";
      render();
      return;
    }
    if (action === "delete-task") {
      await api(`/api/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "已取消" }),
      });
      await refresh("TODO 已删除");
      return;
    }
    if (action === "toggle-notifications") {
      if (!notificationSupported()) {
        showToast("当前浏览器不支持通知");
        return;
      }
      if (Notification.permission !== "granted") {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          state.notificationEnabled = false;
          localStorage.setItem("aylaNotificationsEnabled", "false");
          updateNotificationButton();
          showToast("未开启通知权限");
          return;
        }
      }
      state.notificationEnabled = !state.notificationEnabled;
      localStorage.setItem("aylaNotificationsEnabled", String(state.notificationEnabled));
      updateNotificationButton();
      checkTaskNotifications();
      showToast(state.notificationEnabled ? "TODO 提醒已开启" : "TODO 提醒已关闭");
      return;
    }
    if (action === "copy-agent-token") {
      await navigator.clipboard.writeText(state.data.settings.agent_api_token || "");
      showToast("Agent Token 已复制");
      return;
    }
    if (action === "rotate-agent-token") {
      const ok = window.confirm("重置 OpenClaw 写入 Token？旧 Token 会立即失效。");
      if (!ok) return;
      await api("/api/agent/token/rotate", {
        method: "POST",
        headers: {
          "X-Ayla-Agent-Token": state.data.settings.agent_api_token || "",
        },
        body: "{}",
      });
      await refresh("Agent Token 已重置");
      return;
    }
    if (action === "start-lark-binding") {
      state.larkBinding = await api("/api/connectors/lark/bind/start", {
        method: "POST",
        body: JSON.stringify({}),
      });
      render();
      showToast("已生成飞书扫码授权");
      return;
    }
    if (action === "complete-lark-binding") {
      if (!state.larkBinding?.device_code) {
        showToast("请先生成扫码授权");
        return;
      }
      const result = await api("/api/connectors/lark/bind/complete", {
        method: "POST",
        body: JSON.stringify({ device_code: state.larkBinding.device_code }),
      });
      state.larkBinding = null;
      state.larkStatus = result.status;
      await refresh("飞书工作账号已绑定");
      return;
    }
    if (action === "claim-lark-account") {
      const result = await api("/api/connectors/lark/bind/claim", {
        method: "POST",
        body: "{}",
      });
      state.larkBinding = null;
      state.larkStatus = result.status;
      await refresh("已绑定当前飞书认证账号");
      return;
    }
    if (action === "check-lark") {
      state.larkStatus = await api("/api/connectors/lark/status");
      render();
      const ready = state.larkStatus?.available && state.larkStatus?.auth?.ok && state.larkStatus?.scope_check?.ok;
      showToast(ready ? "飞书数据源可用" : "飞书连接需要处理");
      return;
    }
    if (action === "refresh-libra-experiments") {
      const refreshPromise = loadLibraExperiments(true);
      render();
      await refreshPromise;
      showToast(state.libraExperimentsError || "Libra 重点实验已刷新");
      return;
    }
    if (action === "sync-lark-today" || action === "sync-lark-range") {
      const days = Math.max(1, Math.min(31, Number(state.data.settings.lark_sync_days || 7)));
      const start = action === "sync-lark-today" ? workbenchTodayKey() : workbenchDateKeyOffset(-(days - 1));
      const end = workbenchTodayKey();
      const result = await api("/api/connectors/lark/sync", {
        method: "POST",
        body: JSON.stringify({
          start,
          end,
          days,
          include_calendar: true,
          include_minutes: true,
        }),
      });
      const calendarCreated = result.calendar?.created || 0;
      const minutesCreated = result.minutes?.created || 0;
      const parseAligned = result.minutes?.parse_aligned || 0;
      await refresh(`飞书同步完成：日历 ${calendarCreated} 条，妙记 ${minutesCreated} 条，OKR 对齐 ${parseAligned} 条`);
      return;
    }
    if (action === "confirm-daily-review") {
      const idleText = actionButton.textContent;
      actionButton.disabled = true;
      actionButton.textContent = "归档中";
      actionButton.setAttribute("aria-busy", "true");
      showToast("AI 正在生成归档标题，完成后会更新资产卡片", {
        tone: "info",
        duration: 4200,
      });
      try {
        await api("/api/daily-review/confirm", {
          method: "POST",
          body: JSON.stringify({
            date: actionButton.dataset.date,
            overrides: collectDailyReviewOverrides(),
          }),
        });
        await refresh("今日增量已整理，归档资产已更新", { tone: "success" });
      } finally {
        actionButton.disabled = false;
        actionButton.textContent = idleText;
        actionButton.removeAttribute("aria-busy");
      }
      return;
    }
    if (action === "use-generated-report") {
      const report = document.querySelector("#daily-report");
      if (report) {
        report.value = state.data.today_work_log?.generated_report || state.data.today_work_log?.report || "";
        showToast("已恢复自动日报");
      }
      return;
    }
    if (action === "add-pinned-slot") {
      await api("/api/pinned-slots", {
        method: "POST",
        body: JSON.stringify({ title: "新的固定便笺", category: "待整理", content: "" }),
      });
      await refresh("已新增固定便笺");
      return;
    }
    if (action === "delete-pinned-slot") {
      await api(`/api/pinned-slots/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refresh("固定便笺已删除");
      return;
    }
    if (action === "confirm-task") {
      await api(`/api/inbox/${encodeURIComponent(id)}/confirm-task`, { method: "POST", body: "{}" });
      await refresh("已转为 TODO");
      return;
    }
    if (action === "confirm-note") {
      await api(`/api/inbox/${encodeURIComponent(id)}/confirm-note`, { method: "POST", body: "{}" });
      await refresh("已写入知识库");
      return;
    }
    if (action === "confirm-memory") {
      await api(`/api/inbox/${encodeURIComponent(id)}/confirm-memory`, { method: "POST", body: "{}" });
      await refresh("已写入 AgentMemory");
      return;
    }
    if (action === "remove-ai-archive") {
      await api(`/api/notes/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refresh("资料已移除");
      return;
    }
    if (action === "remove-link-summary") {
      await api(`/api/link-summaries/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refresh("无用资料已删除");
      return;
    }
    if (action === "remove-ai-todo") {
      await api(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refresh("TODO 已移除");
      return;
    }
    if (action === "need-info") {
      await api(`/api/inbox/${encodeURIComponent(id)}/need-info`, { method: "POST", body: "{}" });
      await refresh("已标记需补充");
      return;
    }
    if (action === "ignore") {
      await api(`/api/inbox/${encodeURIComponent(id)}/ignore`, { method: "POST", body: "{}" });
      await refresh("已忽略");
      return;
    }
    if (action === "open-task-source") {
      const url = actionButton.dataset.url || "";
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      return;
    }
    if (action === "complete-task") {
      event.preventDefault();
      await api(`/api/tasks/${encodeURIComponent(id)}/complete`, {
        method: "POST",
        body: "{}",
      });
      state.alarmTaskId = "";
      await refresh("TODO 已完成");
      return;
    }
    if (action === "snooze-task") {
      const minutes = Number(actionButton.dataset.minutes || 10);
      await api(`/api/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ reminder_snoozed_until: datetimeAfterMinutes(minutes) }),
      });
      state.alarmTaskId = "";
      await refresh(`已延时 ${minutes} 分钟`);
      return;
    }
    if (action === "open-task-memory") {
      state.taskMemoryId = id;
      state.alarmTaskId = "";
      setView("dashboard");
      return;
    }
    if (action === "close-task-memory") {
      state.taskMemoryId = "";
      render();
      return;
    }
    if (action === "save-task") {
      const row = document.querySelector(`[data-task-row="${CSS.escape(id)}"]`);
      const payload = {};
      row.querySelectorAll("input[name], select[name], textarea[name]").forEach((input) => {
        payload[input.name] = input.value;
      });
      await api(`/api/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      state.taskEditId = "";
      await refresh("TODO 已保存");
      return;
    }
    if (action === "quick-save-task") {
      const row = document.querySelector(`[data-task-row="${CSS.escape(id)}"]`);
      const status = row?.querySelector('select[name="status"]')?.value || "";
      await api(`/api/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await refresh("TODO 状态已保存");
      return;
    }
    if (action === "select-note") {
      state.selectedNoteId = id;
      render();
      return;
    }
    if (action === "copy-note-path") {
      const note = state.data.notes.find((item) => item.id === id);
      await navigator.clipboard.writeText(note?.path || "");
      showToast("路径已复制");
      return;
    }
    if (action === "copy-note-markdown") {
      const note = state.data.notes.find((item) => item.id === id);
      await navigator.clipboard.writeText(note?.content || "");
      showToast("Markdown 已复制");
      return;
    }
    if (action === "delete-note") {
      const note = state.data.notes.find((item) => item.id === id);
      const ok = window.confirm(`删除知识笔记「${note?.title || ""}」？本地 Markdown 文件也会一起删除。`);
      if (!ok) return;
      await api(`/api/notes/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.selectedNoteId = "";
      await refresh("知识笔记已删除");
      return;
    }
  } catch (error) {
    showToast(error.message);
  }
});

document.addEventListener("change", (event) => {
  const action = event.target.dataset.action;
  if (action === "set-okr-window") {
    state.okrWindow = event.target.value || "7";
    localStorage.setItem("aylaOkrWindow", state.okrWindow);
    render();
    return;
  }
  const prefKey = event.target.dataset.pref;
  if (prefKey) {
    state.prefs[prefKey] = event.target.value;
    applyChromeState();
    saveWorkbenchPrefs();
    showToast("偏好已保存");
    return;
  }
  const taskDueId = event.target.dataset.taskDue;
  if (taskDueId) {
    api(`/api/tasks/${encodeURIComponent(taskDueId)}`, {
      method: "PATCH",
      body: JSON.stringify({
        due_at: event.target.value,
        reminder_snoozed_until: "",
      }),
    })
      .then(() => refresh("DDL 已更新"))
      .catch((error) => showToast(error.message));
    return;
  }
  if (event.target.name === "mode") {
    const form = event.target.closest('[data-form="memo"]');
    if (form) {
      setMemoMode(form, event.target.value);
    }
    return;
  }
  const filter = event.target.dataset.filter;
  if (!filter) return;
  if (filter === "inbox-status") {
    state.inboxFilters.status = event.target.value;
    render();
  }
  if (filter === "inbox-type") {
    state.inboxFilters.type = event.target.value;
    render();
  }
  if (filter === "tasks") {
    state.taskFilter = event.target.value;
    render();
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  const kind = form.dataset.form;
  if (!kind) return;
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form).entries());

  try {
    if (kind === "memo") {
      if (!String(data.content || "").trim()) {
        showToast("请输入备忘内容");
        return;
      }
      if (data.mode === "pinned") {
        await api("/api/pinned-slots", {
          method: "POST",
          body: JSON.stringify({
            title: data.title,
            content: data.content,
            category: data.partition || "待整理",
          }),
        });
        form.reset();
        setMemoMode(form, "auto");
        await refresh("已新增固定便笺");
        return;
      }
      const modelOrganize = isModelOrganizeEnabled();
      setFormBusy(form, true, modelOrganize ? "AI 整理中" : "记录中");
      if (modelOrganize) {
        showToast("AI 正在整理这条记录，完成后会自动更新今日整理", {
          tone: "info",
          duration: 4200,
        });
      }
      try {
        const result = await api("/api/memos", {
          method: "POST",
          body: JSON.stringify(data),
        });
        const modelUsed = Boolean(result?.model_cli?.used);
        form.reset();
        setMemoMode(form, "auto");
        await refresh(
          modelOrganize || modelUsed ? "AI 整理完成，今日整理已更新" : "备忘已自动分类，进入今日整理",
          { tone: modelOrganize || modelUsed ? "success" : "" },
        );
      } finally {
        setFormBusy(form, false);
      }
      return;
    }
    if (kind === "summary") {
      if (!String(data.content || "").trim()) {
        showToast("请输入摘要内容");
        return;
      }
      await api("/api/import/summary", {
        method: "POST",
        body: JSON.stringify(data),
      });
      form.reset();
      await refresh("摘要已导入");
      return;
    }
    if (kind === "daily-log") {
      await api("/api/daily-log", {
        method: "POST",
        body: JSON.stringify(data),
      });
      await refresh("今日记录已保存");
      return;
    }
    if (kind === "quick-task") {
      if (!String(data.title || "").trim()) {
        showToast("请输入 TODO 标题");
        return;
      }
      await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify(data),
      });
      state.todoAddQuadrant = "";
      form.reset();
      const dueInput = form.querySelector('input[name="due_at"]');
      if (dueInput) dueInput.value = defaultTaskDeadlineValue();
      await refresh("今日 TODO 已添加");
      return;
    }
    if (kind === "task-memory") {
      if (!String(data.completion_note || "").trim()) {
        showToast("请记录完成事宜");
        return;
      }
      await api(`/api/tasks/${encodeURIComponent(form.dataset.id)}/complete`, {
        method: "POST",
        body: JSON.stringify(data),
      });
      state.taskMemoryId = "";
      state.alarmTaskId = "";
      await refresh("完成记录已沉淀");
      return;
    }
    if (kind === "settings") {
      await api("/api/settings", {
        method: "POST",
        body: JSON.stringify(data),
      });
      await refresh("设置已保存");
      return;
    }
    if (kind === "pinned-slot") {
      await api(`/api/pinned-slots/${encodeURIComponent(form.dataset.id)}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
      await refresh("固定便笺已保存");
    }
  } catch (error) {
    showToast(error.message);
  }
});

window.addEventListener("hashchange", () => {
  const view = window.location.hash.replace("#", "");
  const nextView = normalizeView(view);
  if (window.location.hash.replace("#", "") !== nextView) {
    window.location.hash = nextView;
    return;
  }
  state.view = nextView;
  render();
});

async function boot() {
  applyChromeState();
  const initial = window.location.hash.replace("#", "");
  const initialView = normalizeView(initial);
  if (window.location.hash.replace("#", "") !== initialView) {
    window.location.hash = initialView;
  }
  state.view = initialView;
  try {
    await refresh();
  } catch (error) {
    root.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
  window.setInterval(checkTaskNotifications, 60 * 1000);
}

window.matchMedia?.("(prefers-color-scheme: dark)")?.addEventListener("change", () => {
  if (state.prefs.uiMode === "system") {
    applyChromeState();
  }
});

window.addEventListener("focus", () => {
  refreshIfNaturalDayChanged().catch((error) => showToast(error.message));
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshIfNaturalDayChanged().catch((error) => showToast(error.message));
  }
});

boot();
